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
import uuid
from datetime import datetime, timezone

import httpx

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
        d = datetime.now(timezone.utc)
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d

from libs.log_manager.controller import LoggingController

from apps.api.api.middlewares.metrics import receipt_events_ingested_total
from apps.api.api.routers.billing.models import Org
from apps.api.core.logging import get_logger

from .billing.plan_limits import session_cap_for
from .db import get_session
from .deps import get_current_user
from .models import (
    Event,
    EventKind,
    EventsBatchIn,
    IngestAck,
    Session as SessionRow,
)
from .pricing import compute_cost_usd, summarize_tokens
from .red_flags import scan_event
from .summary_router import _build_summary


_route_logger = logging.getLogger("apps.api.receipt.routing")


def _resolve_workspace(user: str, cwd: str | None, git_remote: str | None) -> str | None:
    """Resolve the target workspace_id for this event via Supabase.

    Priority order (server-side in resolve_workspace SQL RPC):
      1. workspace_repos exact match on (host, owner, repo) parsed from git_remote
      2. route_rules match on cwd or git_remote globs (escape-hatch)
      3. user's personal workspace (fallback, always succeeds if user has one)

    Returns None only when Supabase is unreachable / user unknown — ingestion
    never blocks on routing; the session lands with workspace_id=NULL and the
    user can move it from the dashboard later.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY", "")
    if not supabase_url or not anon:
        return None
    try:
        resp = httpx.post(
            f"{supabase_url}/rest/v1/rpc/resolve_workspace",
            headers={
                "apikey": anon,
                "Authorization": f"Bearer {anon}",
                "Content-Type": "application/json",
            },
            json={
                "p_user_email": user,
                "p_cwd": cwd,
                "p_git_remote": git_remote,
            },
            timeout=2.0,
        )
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        _route_logger.warning("workspace_rpc_failed err=%s", type(exc).__name__)
        return None
    if resp.status_code >= 400:
        _route_logger.warning(
            "workspace_rpc_status code=%s body=%s",
            resp.status_code, resp.text[:200],
        )
        return None
    try:
        target = resp.json()
    except Exception:
        return None
    return target if isinstance(target, str) and target else None

# tool_name → kind classifier (closes gap #3; see vault/EVENTIN-V1-SPEC.md §2)
_FILE_CHANGE_TOOLS: frozenset[str] = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


def _infer_kind(tool: str | None) -> EventKind:
    if tool in _FILE_CHANGE_TOOLS:
        return "file_change"
    return "tool_use"


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


def _billing_enabled() -> bool:
    """Whether the monthly ingest quota/paywall applies.

    Off by default: a self-hosted instance has no billing, and dropping audit
    records past a free-tier cap would silently lose data (and evidence). Only
    the hosted/billed deployment sets BILLING_ENABLED=true.
    """
    return os.getenv("BILLING_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _month_start_utc() -> datetime:
    """First instant of the current UTC calendar month, naive (SQLite-safe)."""
    now = datetime.now(timezone.utc)
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

        for e in batch.events:
            effective_user = e.user or current_user
            if effective_user is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        "user attribution required: provide 'user' in event "
                        "body or an Authorization bearer token"
                    ),
                )
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
                        started_at=ts,
                    )
                    session.add(sess)
                    session.flush()
                touched[e.session_id] = sess

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

            # First user-prompt message sets the session title. Cheap
            # idempotent: only fires when sess.title is still None.
            if (
                sess.title is None
                and e.kind == "message"
                and e.tool == "user"
                and e.content
            ):
                first_line = next(
                    (ln.strip() for ln in e.content.split("\n") if ln.strip()),
                    "",
                )
                if first_line:
                    sess.title = first_line[:80]

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

            if flags:
                merged = list(sess.flags)
                for f in flags:
                    if f not in merged:
                        merged.append(f)
                sess.flags = merged
                sess.flagged = True
                flagged_ids.add(sess.id)

            # Tamper-evident chain: link each event to the previous one.
            prev_hash = chain_tip.get(e.session_id)
            if prev_hash is None:
                last = session.exec(
                    select(Event)
                    .where(Event.session_id == e.session_id)
                    .order_by(Event.id.desc())
                ).first()
                prev_hash = (last.entry_hash if last and last.entry_hash else "")
            entry_hash = event_entry_hash(
                prev_hash, ts, e.kind, e.tool, e.path, e.content,
                e.tokens_input, e.tokens_output, e.cost_usd,
            )
            chain_tip[e.session_id] = entry_hash

            ev_row = Event(
                session_id=e.session_id,
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
            )
            session.add(ev_row)
            if flags and e.session_id not in first_flagged_event_id:
                session.flush()  # assign id
                if ev_row.id is not None:
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
