"""Events ingestion router for Receipt v0.

Contract: vault/BACKEND-API-V0.md §4.1. One batch = one DB transaction.
Idempotent: duplicate session_id merges aggregates onto the existing row.

Wave-54 V4-1(a) delta: enforces the per-org monthly session cap BEFORE any
DB writes — free orgs that have already created `plan.session_cap` sessions
this UTC calendar month get a `402 Payment Required` with
`{"upgrade_required": "team", "checkout_url": "<polar url>"}`. See
vault/USER_STORIES-v4.md US-V4-1 AC #1.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlmodel import Session as DBSession
from sqlmodel import select


def _naive_utc(d: datetime | None) -> datetime:
    """Normalize to naive UTC — SQLite drops tzinfo so everything in the
    DB is naive; inputs may be aware. Unifying here prevents aware/naive
    comparison errors when merging aggregates across batches."""
    if d is None:
        d = datetime.now(UTC)
    if d.tzinfo is not None:
        d = d.astimezone(UTC).replace(tzinfo=None)
    return d

from apps.api.api.middlewares.metrics import receipt_events_ingested_total
from apps.api.api.routers.billing.models import Org
from apps.api.api.services.retention import retention_expires_at as _retention_expires_at
from apps.api.core.logging import get_logger
from libs.log_manager.controller import LoggingController

from .billing.plan_limits import session_cap_for
from .custom_rules import get_org_rules, scan_custom
from .skill_safety import scan_skill_safety
from .db import get_session
from .auth_router import org_default_workspace_id
from .deps import get_current_default_org_id, get_current_org, get_current_user
from .models import (
    DEFAULT_ORG_ID,
    Event,
    EventFlag,
    EventKind,
    EventsBatchIn,
    IngestAck,
)
from .models import (
    Session as SessionRow,
)
from .pricing import compute_cost_usd, summarize_tokens
from .red_flags import category_of, scan_event, severity_of
from .summary_router import _build_summary

_route_logger = logging.getLogger("apps.api.receipt.routing")


def _parse_git_remote(git_remote: str | None) -> tuple[str, str, str] | None:
    """Parse a git remote URL into (host, owner, repo). Handles both
    `git@github.com:Owner/Repo.git` and `https://github.com/Owner/Repo`."""
    if not git_remote:
        return None
    s = git_remote.strip()
    if s.endswith(".git"):
        s = s[:-4]
    if s.startswith("git@"):
        rest = s[4:]
        if ":" not in rest:
            return None
        host, path = rest.split(":", 1)
    else:
        m = re.match(r"(?:https?|ssh)://(?:[^@/]+@)?([^/]+)/(.+)", s)
        if not m:
            return None
        host, path = m.group(1), m.group(2)
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return host, parts[-2], parts[-1]


def _resolve_workspace(user: str, cwd: str | None, git_remote: str | None) -> str | None:
    """Resolve the target workspace_id for this event, provider-agnostic.

      1. workspace_repos exact match on (host, owner, repo) from git_remote
      2. route_rules match on cwd / git_remote substring (user escape-hatch)
      3. None → session lands unrouted ("Personal"); movable from the dashboard.

    Runs against the configured data store (local or Supabase) — no RPC, so it
    works on a self-hosted instance.
    """
    try:
        from libs.datastore import get_data_store
        store = get_data_store()
    except Exception:
        return None

    parsed = _parse_git_remote(git_remote)
    if parsed:
        host, owner, repo = parsed
        try:
            repos = store.query_records("workspace_repos", filters={"host": host})
        except Exception:
            repos = []
        for r in repos:
            if (str(r.get("owner", "")).lower() == owner.lower()
                    and str(r.get("repo", "")).lower() == repo.lower()):
                ws = r.get("workspace_id")
                if ws:
                    return str(ws)

    # route_rules escape-hatch — caller-scoped substring match on cwd/remote.
    try:
        rules = store.query_records("route_rules", filters={"enabled": True})
    except Exception:
        rules = []
    haystacks = [h for h in (cwd, git_remote) if h]
    for rule in sorted(rules, key=lambda x: x.get("priority", 0)):
        pat = rule.get("match_pattern")
        target = rule.get("target_workspace_id") or rule.get("workspace_id")
        if pat and target and any(pat in h for h in haystacks):
            return str(target)
    return None

# tool_name → kind classifier (closes gap #3; see vault/EVENTIN-V1-SPEC.md §2)
_FILE_CHANGE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _infer_kind(tool: str | None) -> EventKind:
    if tool in _FILE_CHANGE_TOOLS:
        return "file_change"
    return "tool_use"


# A session's title is the headline of its shareable receipt (and the dashboard
# row), so it must read like what the USER asked, not a Claude Code system
# message. When the first "user" event is actually a skill load, a slash-command
# invocation, or a hook-injected banner, its first line is one of these
# preambles — title-deriving from it produces junk like "Base directory for this
# skill: ~/.claude/skills/...". We detect those and decline to title from them,
# leaving the title None so a later, genuine prompt sets it (falling back to the
# session id if none ever arrives).
_TITLE_SKIP_PREFIXES: tuple[str, ...] = (
    "base directory for this skill:",  # skill load preamble
    "<command-name>",                  # slash-command invocation tags
    "<command-message>",
    "<command-args>",
    "caveman mode",                    # hook-injected mode banner
    "<system-reminder>",
)


def _derive_session_title(content: str) -> str | None:
    """Title from the first real line of a user message, or None when that line
    is a non-user-intent preamble (skill load / slash-command / hook banner)."""
    first = next((ln.strip() for ln in content.split("\n") if ln.strip()), "")
    if not first or first.lower().startswith(_TITLE_SKIP_PREFIXES):
        return None
    return first[:80]


def event_entry_hash(prev_hash: str, ts, kind, tool, path, content,
                     tokens_input, tokens_output, cost_usd) -> str:
    """sha256 over the previous hash + this event's immutable content.

    Shared by ingest (to write the chain) and the verifier (to recompute it),
    so both sides hash identically. Volatile/derived fields (flags, raw,
    summaries) are deliberately excluded — they can be recomputed.
    """
    import hashlib
    import json

    canonical = json.dumps(
        {
            "prev": prev_hash or "",
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "kind": kind,
            "tool": tool,
            "path": path,
            "content": content,
            "ti": tokens_input,
            "to": tokens_output,
            "cost": cost_usd,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Chain v2: commit-to-digest (S3, design trovex:28547568 §2) ---------------
#
# The v0 chain (event_entry_hash) commits over the *plaintext* content, so
# nulling content at rest to satisfy GDPR erasure would break tamper-evidence.
# v2 commits over sha256(plaintext) instead: redacting the plaintext leaves the
# committed digest — and therefore the chain — intact and still verifiable.
CHAIN_VERSION_V2 = 2

# Content-bearing fields the v2 chain commits by digest (the redactable channel,
# per design §1a). Everything else (ts/kind/tool/path/tokens/cost) is
# non-sensitive metadata and stays committed in the clear.
_DIGEST_FIELDS: tuple[str, ...] = ("content", "tool_input", "tool_response")


def _field_digest(value) -> str | None:
    """sha256 of one content-bearing field, or None when the field is absent.

    Strings hash as UTF-8 bytes; structured values (raw.tool_input /
    raw.tool_response are arbitrary JSON) hash over a canonical JSON encoding
    so the digest is stable across equal-but-reordered dicts.
    """
    import hashlib
    import json

    if value is None:
        return None
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_content_digests(content, tool_input, tool_response) -> dict[str, str | None]:
    """Per-field content commitments for a v2 event. Keys are always present
    (null when the field was absent at ingest) so the stored shape is stable
    and a later at-rest redaction (S4) is distinguishable from never-present."""
    return {
        "content": _field_digest(content),
        "tool_input": _field_digest(tool_input),
        "tool_response": _field_digest(tool_response),
    }


def event_entry_hash_v2(prev_hash: str, ts, kind, tool, path,
                        digests: dict[str, str | None],
                        tokens_input, tokens_output, cost_usd) -> str:
    """v2 chain hash: same metadata columns as v0, but the content-bearing
    fields are represented by their sha256 digests (a content *commitment*)
    rather than the plaintext. Shared by ingest (write) and verify (recompute)
    so both hash identically. `digests` is sorted into the canonical form so
    dict ordering never affects the result."""
    import hashlib
    import json

    canonical = json.dumps(
        {
            "v": CHAIN_VERSION_V2,
            "prev": prev_hash or "",
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "kind": kind,
            "tool": tool,
            "path": path,
            "digests": {k: digests.get(k) for k in _DIGEST_FIELDS},
            "ti": tokens_input,
            "to": tokens_output,
            "cost": cost_usd,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _billing_enabled() -> bool:
    """Whether the monthly ingest quota/paywall applies.

    Off by default: a self-hosted instance has no billing, and dropping audit
    records past a free-tier cap would silently lose data (and evidence). Only
    the hosted/billed deployment sets BILLING_ENABLED=true.
    """
    return os.getenv("BILLING_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _month_start_utc() -> datetime:
    """First instant of the current UTC calendar month, naive (SQLite-safe)."""
    now = datetime.now(UTC)
    return now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )


def _count_sessions_this_month(db: DBSession, user: str) -> int:
    """Count `Session` rows for `user` with `started_at >= month-start-UTC`."""
    month_start = _month_start_utc()
    return int(
        db.exec(
            select(func.count())
            .select_from(SessionRow)
            .where(SessionRow.user == user)
            .where(SessionRow.started_at >= month_start)
        ).one()
    )


def _build_paywall_checkout_url(org_id: str) -> str:
    """Mint a Polar-hosted upgrade URL for the team plan.

    Imported lazily so tests can `monkeypatch.setattr` on
    `apps.api.api.routers.billing.checkout._polar_client` AFTER this module
    has loaded. Returns `str(response.url)` — with the real SDK that's the
    Polar-hosted checkout URL; with the default `MagicMock` it's whatever the
    test swapped in.
    """
    from apps.api.api.routers.billing import checkout as checkout_mod

    plan_id = os.environ.get(
        checkout_mod._PLAN_ID_ENV["team"],
        checkout_mod._PLAN_ID_DEFAULT["team"],
    )
    base = os.environ.get("RECEIPT_DASHBOARD_URL", "http://localhost:5173").rstrip("/")
    response = checkout_mod._polar_client.checkouts.create(
        plan_id=plan_id,
        success_url=f"{base}/settings/billing?upgraded=1",
        cancel_url=f"{base}/settings/billing",
        client_reference_id=org_id,
    )
    return str(response.url)


_QUOTA_ORG_NS = uuid.UUID("0e7b0f20-6b2f-4a6a-b0a1-0cbf3d1e7f00")


def _resolve_quota_org_id(user: str) -> str:
    """Deterministic UUID5 per user string — 1:1 personal-org mapping for the
    Receipt v0 quota path. Kept here since the Polar checkout flow now uses
    the real Supabase auth.users.id as customer_external_id."""
    return str(uuid.uuid5(_QUOTA_ORG_NS, user))


class EventsRouter:
    """POST /sessions/events — batch ingest from the Claude Code hook."""

    def __init__(self) -> None:
        self.logger = LoggingController(app_name="receipt_events_router")
        self._log = get_logger("apps.api.receipt.events")
        self.router = APIRouter(prefix="/sessions", tags=["receipt:events"])
        self._setup_routes()
        self.logger.log_info("Receipt events router initialized")

    def get_router(self) -> APIRouter:
        return self.router

    def initialize_services(self) -> None:
        pass

    def _setup_routes(self) -> None:
        self.router.post(
            "/events",
            response_model=IngestAck,
            status_code=status.HTTP_202_ACCEPTED,
        )(self.ingest)

    def ingest(
        self,
        batch: EventsBatchIn,
        session: DBSession = Depends(get_session),
        current_user: str | None = Depends(get_current_user),
        current_org: str | None = Depends(get_current_org),
        current_default_org_id: str | None = Depends(get_current_default_org_id),
    ) -> IngestAck | JSONResponse:
        """Persist a batch of events + update session aggregates atomically.

        User attribution: `event.user` wins when set (v0 trust-body contract,
        used by scripts/smoke-us14.sh). Otherwise the bearer-derived user
        from deps.get_current_user is used. If neither is present the batch
        is rejected 422 — closes the silent-ingest-zero-events failure mode
        (vault/audits/us14-activation-smoke.md §real-hook gap #1).
        """
        touched: dict[str, SessionRow] = {}
        flagged_ids: set[str] = set()
        # Track first flagged event per session so the notification anchor can
        # deep-link right into the event that triggered the flag. Populated
        # after the Event row gets an id via session.flush().
        first_flagged_event_id: dict[str, int] = {}
        accepted = 0

        self._log.info("events.received", extra={"batch_size": len(batch.events)})

        # M2b (multi-tenant security, design 44a3774a §4): attribution is a
        # verified system property, never self-claimed. Ingest REQUIRES a
        # credential (bearer token / API key / session cookie); the anonymous
        # v0 "trust EventIn.user" path is closed. No credential → 401 for the
        # whole batch, before any DB write.
        if current_user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "authenticated credential required for ingest: send an "
                    "Authorization bearer token, X-API-Key, or session cookie"
                ),
            )

        # Quota paywall (US-V4-1 AC #1). Fires BEFORE any DB writes so a 402
        # leaves no partial state behind. Keyed on the authenticated user when
        # present; falls back to the first event's `user` for v0 trust-body
        # callers (scripts/smoke-us14.sh) so those still get rate-limited.
        quota_user = current_user or (batch.events[0].user if batch.events else None)
        if _billing_enabled() and quota_user is not None:
            org_id = _resolve_quota_org_id(quota_user)
            org = session.get(Org, org_id)
            plan = org.plan if org is not None else "free"
            cap = session_cap_for(plan)
            if cap is not None:
                count = _count_sessions_this_month(session, quota_user)
                if count >= cap:
                    self._log.info(
                        "events.quota_exceeded",
                        extra={"plan": plan, "count": count, "cap": cap},
                    )
                    return JSONResponse(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        content={
                            "upgrade_required": "team",
                            "checkout_url": _build_paywall_checkout_url(org_id),
                        },
                    )

        # Per-session hash-chain tip. Seeded lazily from the last persisted
        # event so the chain continues across batches.
        chain_tip: dict[str, str] = {}

        # Dedup keys present in this batch (tailer-origin events carry a stable
        # entry_uuid). Seed from the DB so re-reading a transcript is idempotent.
        batch_uuids = {e.entry_uuid for e in batch.events if e.entry_uuid}
        seen_uuids: set[str] = set()
        if batch_uuids:
            sids = {e.session_id for e in batch.events}
            existing = session.exec(
                select(Event.entry_uuid).where(
                    Event.session_id.in_(sids),
                    Event.entry_uuid.in_(batch_uuids),
                )
            ).all()
            seen_uuids.update(u for u in existing if u)

        for e in batch.events:
            # Skip events we already have (idempotent re-read after downtime).
            if e.entry_uuid:
                if e.entry_uuid in seen_uuids:
                    continue
                seen_uuids.add(e.entry_uuid)

            # M2b: the verified identity wins. A body-supplied `user` that
            # disagrees with the authenticated identity is tampering, not data
            # (code-is-law) → 403. A matching or absent `user` is fine.
            if e.user and e.user != current_user:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "attribution mismatch: event 'user' does not match the "
                        "authenticated identity"
                    ),
                )
            effective_user = current_user
            ts = _naive_utc(e.ts)
            if e.kind is None:
                e.kind = _infer_kind(e.tool)
            # Extract path + content from raw.tool_input for all known tool shapes.
            # (EVENTIN-V1-SPEC §2.b for file_path; extended here for Bash/Grep/Read/WebSearch.)
            raw_input = (e.raw or {}).get("tool_input")
            if isinstance(raw_input, dict):
                if e.path is None:
                    p = raw_input.get("file_path") or raw_input.get("path") or raw_input.get("notebook_path")
                    if isinstance(p, str) and p:
                        e.path = p
                if e.content is None:
                    # Bash → command; Grep → pattern; Read → path (already captured); WebSearch/WebFetch → query/url
                    c = (
                        raw_input.get("command")
                        or raw_input.get("pattern")
                        or raw_input.get("query")
                        or raw_input.get("url")
                        or raw_input.get("old_string")
                        or raw_input.get("new_string")
                        or raw_input.get("content")
                    )
                    if isinstance(c, str) and c:
                        e.content = c[:400]  # cap at 400 chars for display

            # Auto-compute cost + tokens for kind=token events (transcript
            # tailer ships raw usage + model, pricing lookup happens here so
            # rates stay centralized and auto-refreshed from LiteLLM).
            if e.kind == "token":
                raw = e.raw if isinstance(e.raw, dict) else {}
                usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
                model = raw.get("model") or ""
                if usage and isinstance(model, str):
                    if e.tokens_input == 0 and e.tokens_output == 0:
                        t_in, t_out = summarize_tokens(usage)
                        e.tokens_input = t_in
                        e.tokens_output = t_out
                    if e.cost_usd == 0.0:
                        e.cost_usd = compute_cost_usd(model, usage)

            flags = scan_event(e)

            sess = touched.get(e.session_id)
            if sess is None:
                sess = session.get(SessionRow, e.session_id)
                if sess is None:
                    sess = SessionRow(
                        id=e.session_id,
                        user=effective_user,
                        # M2: pin the tenant org from the verified credential;
                        # legacy/anon tokens (no org) fall back to the default
                        # org. Frozen at session creation like workspace_id.
                        org_id=current_org or DEFAULT_ORG_ID,
                        started_at=ts,
                    )
                    session.add(sess)
                    session.flush()
                touched[e.session_id] = sess

            # Custom red-flag rules (design trovex:961a5e80, task 569f1d47):
            # org-scoped, evaluated after the six presets. TTL-cached per org
            # (custom_rules.get_org_rules) so this doesn't add a query per
            # event. custom_severity records each hit's user-configured
            # severity so the EventFlag write below doesn't need to re-query
            # the rule (severity_of can't derive it from category alone).
            custom_hits = scan_custom(e, get_org_rules(session, sess.org_id))
            custom_severity: dict[str, str] = {}
            for rule_id, sev in custom_hits:
                if rule_id not in flags:
                    flags.append(rule_id)
                custom_severity[rule_id] = sev

            # Skill-safety rules (design cto 2026-08-21, task fa3baa27):
            # built-in deterministic catalog (16 rules), evaluated after
            # custom rules. No DB/cache — scan_skill_safety no-ops unless the
            # event touches skill surface (SKILL.md / .claude/skills/ /
            # settings.json), so this is zero-cost on the vast majority of
            # events. Severity is embedded in the catalog (skill_safety.py),
            # not caller-supplied like custom_severity above.
            for rule_id in scan_skill_safety(e):
                if rule_id not in flags:
                    flags.append(rule_id)

            # Phase C/W1 — routing: capture cwd/git context on first event of
            # a session and resolve the target workspace via resolve_workspace
            # RPC (workspace_repos → route_rules → personal fallback). Frozen
            # once set so later `cd`s mid-session don't re-route.
            if sess.workspace_id is None:
                if e.cwd or e.git_remote:
                    sess.cwd = e.cwd
                    sess.git_remote = e.git_remote
                    sess.git_branch = e.git_branch
                sess.workspace_id = _resolve_workspace(
                    user=effective_user,
                    cwd=e.cwd,
                    git_remote=e.git_remote,
                )
                # Multi-dev identity model (DEC-yoru-design-ruling-1 A.3#2).
                # An event that matches neither workspace_repos nor
                # route_rules falls back to the calling token's
                # default_org_id (resolved to that org's default workspace)
                # instead of going unattributed ("Personal"). NEVER runs when
                # _resolve_workspace already matched — additive only, zero
                # change for any event that already routes. Fails open to
                # unrouted (None) on any resolution error — a bad/unreachable
                # fallback must never break ingest.
                if sess.workspace_id is None and current_default_org_id:
                    try:
                        sess.workspace_id = org_default_workspace_id(current_default_org_id)
                    except Exception:
                        self._log.warning(
                            "events.default_org_fallback_failed",
                            extra={"org_id": current_default_org_id},
                        )

            # First user-prompt message sets the session title. Cheap
            # idempotent: only fires when sess.title is still None. Skips
            # skill-load / slash-command / hook preambles so the title reads
            # like a real prompt (see _derive_session_title); when this message
            # is a preamble, title stays None for a later genuine prompt.
            if (
                sess.title is None
                and e.kind == "message"
                and e.tool == "user"
                and e.content
            ):
                derived = _derive_session_title(e.content)
                if derived:
                    sess.title = derived

            # Push started_at backward on ANY event with an earlier ts.
            # Fixes backfill: the hook-ingested events land first and set
            # started_at to "now"; a later transcript backfill carries
            # events from days earlier and must win. (Previously only
            # session_start events could push the boundary back.)
            if ts < sess.started_at:
                sess.started_at = ts
                # Summary captured earlier with a partial view is stale —
                # clear so the next session_end rebuilds it with the full
                # backfilled dataset.
                sess.summary = None

            if e.kind == "session_end":
                pass
            elif e.kind == "tool_use":
                sess.tools_count += 1
                if e.tool and e.tool not in sess.tools_called:
                    sess.tools_called = [*sess.tools_called, e.tool]
            elif e.kind == "file_change" and e.path:
                if e.path not in sess.files_changed:
                    sess.files_count += 1
                    sess.files_changed = [*sess.files_changed, e.path]

            sess.tokens_input += e.tokens_input
            sess.tokens_output += e.tokens_output
            sess.cost_usd += e.cost_usd
            if sess.ended_at is None or ts > sess.ended_at:
                sess.ended_at = ts

            # Compliance retention (S2). Stamp the session's expiry from its
            # (possibly-backfilled) start + policy; recomputed each event so a
            # later earlier-ts backfill moves the expiry back with started_at.
            sess.retention_expires_at = _retention_expires_at(sess.started_at)

            if flags:
                merged = list(sess.flags)
                for f in flags:
                    if f not in merged:
                        merged.append(f)
                sess.flags = merged
                sess.flagged = True
                flagged_ids.add(sess.id)

            # Tamper-evident chain (v2, commit-to-digest): link each event to
            # the previous one, but commit over sha256(content-bearing field)
            # not the plaintext — so at-rest redaction (S4) leaves the chain
            # verifiable. prev_hash linkage is version-agnostic, so a new v2
            # event chains cleanly onto a legacy v0 tail.
            prev_hash = chain_tip.get(e.session_id)
            if prev_hash is None:
                last = session.exec(
                    select(Event)
                    .where(Event.session_id == e.session_id)
                    .order_by(Event.id.desc())
                ).first()
                prev_hash = (last.entry_hash if last and last.entry_hash else "")
            raw_dict = e.raw if isinstance(e.raw, dict) else {}
            digests = compute_content_digests(
                e.content, raw_dict.get("tool_input"), raw_dict.get("tool_response"),
            )
            entry_hash = event_entry_hash_v2(
                prev_hash, ts, e.kind, e.tool, e.path, digests,
                e.tokens_input, e.tokens_output, e.cost_usd,
            )
            chain_tip[e.session_id] = entry_hash

            import json as _json
            ev_row = Event(
                session_id=e.session_id,
                org_id=sess.org_id,  # M2: denormalize the tenant org from the session
                ts=ts,
                kind=e.kind,
                tool=e.tool,
                path=e.path,
                content=e.content,
                tokens_input=e.tokens_input,
                tokens_output=e.tokens_output,
                cost_usd=e.cost_usd,
                flags=flags,
                raw=e.raw,
                cwd=e.cwd,
                git_remote=e.git_remote,
                git_branch=e.git_branch,
                prev_hash=prev_hash,
                entry_hash=entry_hash,
                chain_version=CHAIN_VERSION_V2,
                content_digest=_json.dumps(digests, sort_keys=True),
                entry_uuid=e.entry_uuid,
                retention_expires_at=_retention_expires_at(ts),
            )
            session.add(ev_row)
            # First-class red-flag records (S2). Written ALONGSIDE the JSON
            # arrays (kept in sync) so the risk log is queryable without
            # unpacking JSON. Needs the event id, so flush when flags exist —
            # this also assigns the first-flagged-event id below.
            if flags:
                session.flush()  # assign ev_row.id
                for rule_id in flags:
                    session.add(EventFlag(
                        session_id=e.session_id,
                        org_id=sess.org_id,  # M2: per-org risk log
                        event_id=ev_row.id,
                        rule_id=rule_id,
                        category=category_of(rule_id),
                        severity=severity_of(rule_id, custom_severity.get(rule_id)),
                        ts=ts,
                        # steal#6: self-explanatory record — freeze the parent
                        # session's git context onto the flag (which repo/branch/
                        # dir the flagged action ran in), no join needed.
                        git_branch=sess.git_branch,
                        git_remote=sess.git_remote,
                        cwd=sess.cwd,
                    ))
                if e.session_id not in first_flagged_event_id and ev_row.id is not None:
                    first_flagged_event_id[e.session_id] = ev_row.id
            receipt_events_ingested_total.labels(
                kind=e.kind or "unknown",
                flagged=str(bool(flags)).lower(),
            ).inc()
            accepted += 1

        session.commit()

        # Rebuild summary for every session touched in this batch. The
        # previous gates (summary=None, or session_end only) froze the
        # summary mid-backfill with partial totals — and backfills send 50
        # events at a time, so by the time aggregates fully settle the first
        # batch's summary is long stale. Per-batch rebuild is cheap and
        # keeps the summary honest.
        for sid, sess in touched.items():
            if sess is None:
                continue
            sess.summary = _build_summary(sess)
            session.add(sess)
        session.commit()

        self._log.info(
            "events.ingested",
            extra={
                "accepted": accepted,
                "sessions": len(touched),
                "flagged": len(flagged_ids),
            },
        )

        # Fire in-app notifications for any session that picked up a red flag
        # during this batch. Best-effort — ingest ack is the SLO, notification
        # failure is logged and swallowed. Dedup per session: one notification
        # per (session, flag-kind) not per event.
        if flagged_ids and current_user:
            from .notify import notify_user_by_email
            for sid in flagged_ids:
                sess = touched.get(sid)
                if sess is None:
                    continue
                flags_summary = ", ".join(sess.flags[:3])
                more = len(sess.flags) - 3
                if more > 0:
                    flags_summary += f" · +{more} more"
                # Anchor to the first flagged event when we have one so the
                # dashboard scrolls + flashes directly on the problem line
                # (see SessionDetailPage hash handler).
                ev_id = first_flagged_event_id.get(sid)
                action_url = f"/s/{sid}#event-{ev_id}" if ev_id else f"/s/{sid}"
                notify_user_by_email(
                    email=current_user,
                    type="warning",
                    title=f"Red flag in session {sid[:8]}",
                    message=f"Detected: {flags_summary}. Review trail before merging.",
                    action_url=action_url,
                    metadata={"session_id": sid, "flags": sess.flags, "event_id": ev_id},
                )

        return IngestAck(
            accepted=accepted,
            session_ids=list(touched.keys()),
            flagged_sessions=sorted(flagged_ids),
        )
