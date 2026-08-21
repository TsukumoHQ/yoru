"""SQLModel tables + request/response schemas for Receipt v0.

Single source of truth for all Receipt shapes. Routers import from here.
Contract frozen in vault/BACKEND-API-V0.md §2–§3.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import model_validator
from sqlmodel import JSON, Column, Field, SQLModel

EventKind = Literal[
    "tool_use",
    "file_change",
    "token",
    "error",
    "message",          # UserPromptSubmit / Notification / SubagentStop — human-facing text
    "session_start",
    "session_end",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Sentinel tenant key for pre-multi-tenant (legacy) audit rows — the studio's
# own / default org. Backfilled onto every un-scoped session/event/flag by the
# M1 migration (design 44a3774a §2). Real client-orgs carry their
# organizations.id UUID; this readable sentinel keeps legacy rows visibly
# distinct from a real client-org so they're never mistaken for one.
DEFAULT_ORG_ID = "default-org"


# ---------- Tables ----------

class Session(SQLModel, table=True):
    """Denormalized per-agent-session row."""
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    user: str = Field(index=True)
    # Multi-tenant tenant key (design 44a3774a §2 / M1). The client-org this
    # session belongs to — one yoru instance hosts N isolated client-orgs. Set
    # at ingest from the acting dev's org-bound token (M2), never self-claimed.
    # A session belongs to exactly ONE org, so the per-session hash-chain stays
    # sound per-org (org_id is scoping metadata, not part of canonical(event)).
    # NULL only on legacy pre-multi-tenant rows until backfilled to the default
    # org. NB: the name `org_id` was historically a *routing* column renamed to
    # `workspace_id` (see db.py) — this is the distinct TENANT key. The index is
    # managed in db.init_db (must be re-created after db.py's DROP INDEX
    # ix_sessions_org_id cleanup), so no index=True here.
    org_id: Optional[str] = Field(default=None)
    agent: str = Field(default="claude-code")
    started_at: datetime = Field(default_factory=_utcnow, index=True)
    ended_at: Optional[datetime] = Field(default=None)
    tools_count: int = 0
    files_count: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = Field(default=0.0, index=True)
    flagged: bool = Field(default=False, index=True)
    flags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    files_changed: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    tools_called: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    summary: Optional[str] = Field(default=None)
    # Human-readable title — auto-derived from the first user prompt (first
    # 80 chars). Persisted on first user event; users can PATCH to override.
    title: Optional[str] = Field(default=None)
    # Routing target — the workspace this session belongs to. Resolved
    # server-side via resolve_workspace RPC at first event and frozen for the
    # session lifetime. NULL means routing couldn't resolve (unusual;
    # normally the user's personal workspace is the fallback).
    workspace_id: Optional[str] = Field(default=None, index=True)
    # Routing context sampled at first event — kept for display ("this session
    # ran in ~/work/acme-app on main") and re-routing when rules change.
    cwd: Optional[str] = Field(default=None)
    git_remote: Optional[str] = Field(default=None, index=True)
    git_branch: Optional[str] = Field(default=None)
    # Opt-in public share flag (issue #79). Default false — every session
    # starts private. Flipped by POST /sessions/{id}/share. Gates read
    # access on GET /public/sessions/{id} (unauth, redacted).
    is_public: bool = Field(default=False, index=True)
    # Compliance retention (S2, design trovex:28547568 §3). UTC instant past
    # which this session is eligible for retention action. Recomputed at each
    # ingest from started_at + instance policy (started_at can move back on
    # backfill). NULL = keep forever. S5 adds the >=6mo floor + enforcement.
    retention_expires_at: Optional[datetime] = Field(default=None)


class Event(SQLModel, table=True):
    """Append-only event stream keyed by session."""
    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, foreign_key="sessions.id")
    # Multi-tenant tenant key (M1) — denormalized from the parent session (a
    # session is single-org) so per-org red-flag/retention scans don't join.
    # Index managed in db.init_db (idempotent, covers the ALTER-added column on
    # existing DBs where create_all won't add it).
    org_id: Optional[str] = Field(default=None)
    ts: datetime = Field(default_factory=_utcnow, index=True)
    kind: str = Field(index=True)
    tool: Optional[str] = None
    path: Optional[str] = None
    content: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    flags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    raw: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # Routing context per event (Phase C) — hook includes these so the
    # server can (a) route the session to the right org at first event and
    # (b) detect when cwd changes within a session (e.g. user ran `cd`).
    cwd: Optional[str] = Field(default=None)
    git_remote: Optional[str] = Field(default=None)
    git_branch: Optional[str] = Field(default=None)
    # Tamper-evident hash chain (per session, ordered by id). `entry_hash` =
    # sha256(prev_hash + canonical(event)); `prev_hash` links to the previous
    # event. A later edit/delete/reorder breaks the chain — verifiable via
    # GET /sessions/{id}/verify. This is what makes the trail court-usable.
    entry_hash: Optional[str] = Field(default=None)
    prev_hash: Optional[str] = Field(default=None)
    # Chain version marker (S3, design trovex:28547568 §2). NULL/0 = legacy
    # v0 rows whose chain commits over PLAINTEXT content — redacting them
    # breaks the chain. `2` = commit-to-digest: the chain commits over
    # sha256(content-bearing field), never the plaintext, so redacting the
    # plaintext at rest leaves the chain intact and still verifiable
    # (reconciles Art.12 tamper-evidence with GDPR Art.17 erasure). Existing
    # v0 rows are NEVER rewritten to v2 — rewriting history is itself
    # indistinguishable from tampering.
    chain_version: Optional[int] = Field(default=None)
    # v2 content commitments: JSON {"content": <sha256|null>,
    # "tool_input": <sha256|null>, "tool_response": <sha256|null>}. Each
    # non-null digest is what the v2 chain committed to at ingest. When the
    # plaintext is still present verify cross-checks sha256(plaintext) against
    # it (catches plaintext edits); once the plaintext is redacted/nulled (S4)
    # the committed digest carries the chain on its own. NULL on v0 rows.
    content_digest: Optional[str] = Field(default=None)
    # Stable per-event dedup key from the source transcript (line uuid + block
    # index). Lets the tailer re-read a transcript idempotently — the backend
    # skips an (session_id, entry_uuid) it already has — so events survive
    # backend downtime without ever double-inserting.
    entry_uuid: Optional[str] = Field(default=None, index=True)
    # Compliance retention (S2, design trovex:28547568 §3). UTC instant past
    # which this event is eligible for retention action (S5 enforces; here it's
    # recorded at ingest from instance policy). NULL = keep forever
    # (RETENTION_DAYS=0). Additive column; absent on pre-S2 rows.
    retention_expires_at: Optional[datetime] = Field(default=None)


class EventFlag(SQLModel, table=True):
    """First-class red-flag record (S2, design trovex:28547568 §6).

    Red flags were only ever a JSON string array on ``events.flags`` /
    ``sessions.flags`` — fine for display, but not queryable as a risk log
    (AI Act Art.12(2) risk-ID + monitoring; NIST AI RMF risk log). This
    promotes each triggered rule to a row so an auditor can query
    "every db_destructive across the fleet in Q3" without unpacking JSON.

    Written at ingest ALONGSIDE the JSON arrays (both kept in sync); the JSON
    stays the read-perf/back-compat path, this is the queryable index. One row
    per (event, rule_id). ``category`` is the load-bearing six-kind taxonomy
    (secret · shell · db · env · migration · ci); ``severity`` is the coarse
    audit tier derived from that category.
    """
    __tablename__ = "event_flags"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, foreign_key="sessions.id")
    event_id: Optional[int] = Field(default=None, index=True, foreign_key="events.id")
    # Multi-tenant tenant key (M1) — denormalized from the parent session so an
    # auditor can query the per-org risk log without a join. Index in db.init_db.
    org_id: Optional[str] = Field(default=None)
    rule_id: str = Field(index=True)
    category: str = Field(index=True)   # one of the six user-facing kinds
    severity: str = Field(index=True)   # critical | high | medium
    ts: datetime = Field(default_factory=_utcnow, index=True)
    # steal#6 (study 6dcc43ce, TraceRoot): git context denormalized from the
    # parent session at write time, so a red-flag record is self-explanatory —
    # an auditor sees WHICH repo/branch/dir the flagged action ran in without a
    # join to sessions. Same denormalization rationale as org_id above. A HEAD
    # commit sha is NOT captured at ingest today (the session freezes only
    # remote/branch/cwd); adding it is a follow-up that needs the CLI hook to
    # send it.
    git_branch: Optional[str] = Field(default=None)
    git_remote: Optional[str] = Field(default=None)
    cwd: Optional[str] = Field(default=None)


class CustomRule(SQLModel, table=True):
    """Org-defined red-flag rule (design trovex:961a5e80, task 569f1d47).

    Generalizes the six built-in presets (``red_flags.py``) into a
    user-editable ruleset, scoped per org (the existing tenant boundary —
    ``CliToken``/``EventFlag.org_id``; "each user" in the founder brief reads
    as "each customer org", cto ruling 2026-08-21). A hit is recorded as an
    ``EventFlag`` with ``category="custom"`` (additive 7th value — the six
    preset categories are untouched) and ``rule_id="custom:{id}"`` so it can
    never collide with or be mistaken for a preset id.

    v1 match_type is deliberately narrow: ``contains`` (plain substring) and
    ``path_glob`` only — both linear-time. ``regex`` is rejected at create
    time (cto ruling: ReDoS on user-authored patterns against shared ingest
    infra is a real ticket, not a v1 corner-cut; fast-follow gated on a
    linear-time engine e.g. google-re2).
    """
    __tablename__ = "custom_rules"

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, primary_key=True)
    org_id: str = Field(index=True)
    name: str
    enabled: bool = Field(default=True)
    # Optional narrowing filters — None means "any". kind mirrors EventIn.kind;
    # tool_filter is a JSON list of tool names (e.g. ["Bash","Shell"]).
    kind_filter: Optional[str] = Field(default=None)
    tool_filter: Optional[list[str]] = Field(default=None, sa_column=Column(JSON))
    match_type: str  # "contains" | "path_glob" — regex rejected at create time (v1)
    pattern: str = Field(max_length=512)  # create-time length cap (cache-size + cost guard)
    severity: str  # "critical" | "high" | "medium" — reuses the existing 3-tier enum
    created_by: str  # user id/email — audits authorship of the rule itself
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CliToken(SQLModel, table=True):
    """Opaque token for the Receipt CLI hook. Two flavors live in the same
    table (Phase B):

      - `token_type='user'` — minted by device-code pairing, `user` holds the
        minter's email, `org_id` is NULL. Dies if the human leaves all orgs.
      - `token_type='service'` — minted by an org admin from the dashboard,
        `org_id` is set and `user` is a synthetic marker; `minted_by_user_id`
        records the human admin who created it. Survives user departures —
        intended for CI/server/fleet deployments.

    Event scope is NOT stored on the token. It is resolved server-side at
    ingest via `route_rules` in Supabase.
    """
    __tablename__ = "cli_tokens"

    id: str = Field(primary_key=True)
    user: str = Field(index=True)
    token_hash: str = Field(index=True, unique=True)
    token_type: str = Field(default="user", index=True)  # 'user' | 'service'
    # Multi-tenant tenant key (M2, design 44a3774a §4). The client-org this
    # token is bound to — token = (user, org_id). Ingest stamps sessions.org_id
    # from this, so a client-org's devs can only write into their own org. NULL
    # on legacy tokens (→ ingest falls back to DEFAULT_ORG_ID). Distinct from
    # `workspace_id` below, which is the *routing* target (itself the field the
    # old routing-`org_id` was renamed to); this is the TENANT key.
    org_id: Optional[str] = Field(default=None, index=True)
    workspace_id: Optional[str] = Field(default=None, index=True)  # set if service — target workspace for fleet tokens
    minted_by_user_id: Optional[str] = Field(default=None)  # audit trail
    machine_hostname: Optional[str] = Field(default=None, max_length=256)
    label: Optional[str] = Field(default=None, max_length=128)
    # Multi-dev identity model (DEC-yoru-design-ruling-1 A.3#1). This row IS
    # the device/session identity — identity_label is its friendly name (the
    # thing `yoru use` would list), distinct from the legacy `label` field
    # other CliToken consumers (mint_token, service tokens) already depend
    # on. Round-tripped from the same client default (`<hostname> · <os>`,
    # init_cmd.py:_default_label) but free to diverge later (rename an
    # identity without touching `label`'s existing contract).
    identity_label: Optional[str] = Field(default=None, max_length=128)
    scopes: Optional[str] = Field(default=None)  # JSON: ['events:write', ...]
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


# Back-compat alias so existing call sites (deps, auth_router) keep working
# during the Phase B rollout. New code should import CliToken.
HookToken = CliToken


class ApiKey(SQLModel, table=True):
    """Long-lived API key for headless / CI / programmatic access.

    Unlike hook-tokens (minted via device-code pairing), API keys are minted
    from an already-authenticated dashboard or CLI-token session and pasted
    into server config or a CI secret store. The raw value (`yoru_ak_*`) is
    returned ONCE at creation; only its sha256 persists. `key_prefix` (the 8
    chars after the prefix) is stored so the UI can identify a key without
    revealing it. The raw key is a full bearer credential — treat it like a
    password, never log it.

    Scopes are ENFORCED at resolution (deps._resolve_api_key), not decorative:
      - 'ingest' → POST /sessions/events only (the CI/runner case, default)
      - 'read'   → GET/HEAD on the receipt surface
    Any other verb is refused for API-key callers, which structurally locks
    them out of credential-minting endpoints; `deny_api_key_auth` remains as
    defense-in-depth on those endpoints.
    """
    __tablename__ = "api_keys"

    id: str = Field(primary_key=True)
    user: str = Field(index=True)
    key_hash: str = Field(index=True, unique=True)
    key_prefix: str = Field(index=True)
    # Multi-tenant tenant key (M2, design 44a3774a §4) — the client-org this key
    # writes into; ingest stamps sessions.org_id from it. NULL on legacy keys
    # (→ DEFAULT_ORG_ID fallback).
    org_id: Optional[str] = Field(default=None, index=True)
    label: Optional[str] = Field(default=None, max_length=128)
    scopes: str = Field(default='["ingest"]')  # JSON list ⊆ API_KEY_SCOPES
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


# The only scopes an API key can carry — enforcement lives in
# deps._resolve_api_key, creation-time validation in auth_router.
API_KEY_SCOPES: frozenset[str] = frozenset({"ingest", "read"})


class DeviceAuthorizationToken(SQLModel, table=True):
    """Transient store for the raw hook-token minted at /device-code/approve.

    Lifetime: from approve (~T0) to first /poll reading an approved row
    (~T0+2–10s). After that the row is deleted and only the sha256 persists
    on `DeviceAuthorization.token_hash`.

    Exists to replace the pre-beta `!`-sentinel pattern where the raw token
    was transiently stored on the pairing row itself under the `token_hash`
    column. Putting the raw value in a column *named* `token_hash` was
    fragile — any tool that dumped that table assumed hashes, not plaintext.
    A dedicated table makes the transience explicit and auditable.

    Why not Redis: no Redis dependency in Receipt v0. Why not KMS: one extra
    moving part for a ~10s window. A tiny SQLModel table + scheduled purge
    is the smallest design that removes the footgun.
    """
    __tablename__ = "device_authorization_tokens"

    device_code_hash: str = Field(primary_key=True)
    raw_token: str
    expires_at: datetime = Field(index=True)


class DeviceAuthorization(SQLModel, table=True):
    """OAuth-2-style device-code pairing row (RFC 8628 simplified).

    Lifecycle:
      1. CLI calls POST /auth/device-code (unauth) → row created with
         status='pending', user=NULL, token_hash=NULL.
      2. User opens /cli/pair in an authenticated browser, enters `user_code`,
         confirms. Frontend calls POST /auth/device-code/approve → row flips
         to status='approved', user is set, a `rcpt_*` hook-token is minted
         and its hash stored in this row.
      3. CLI polls POST /auth/device-code/poll with the raw `device_code`.
         First 'approved' poll returns the raw token and transitions row to
         status='consumed' (token is read-once; subsequent polls get 'denied').

    `device_code` is the long random secret the CLI holds; only its sha256 is
    stored. `user_code` is the short human-type code (e.g. ABCD-EFGH) shown on
    the CLI and typed by the user in the browser; stored in clear so approve
    can look it up.
    """
    __tablename__ = "device_authorizations"

    id: str = Field(primary_key=True)
    device_code_hash: str = Field(index=True, unique=True)
    user_code: str = Field(index=True, unique=True, max_length=16)
    status: str = Field(default="pending", index=True)  # pending|approved|consumed|expired|denied
    user: Optional[str] = Field(default=None, index=True)
    token_hash: Optional[str] = None  # sha256 of the hook_token, for audit only
    label: Optional[str] = Field(default=None, max_length=128)
    # Raw machine hostname (socket.gethostname(), NOT the friendly label —
    # that's user-overridable via --label, this isn't). Carried through to
    # CliToken.machine_hostname at approve.
    hostname: Optional[str] = Field(default=None, max_length=256)
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime = Field(index=True)
    approved_at: Optional[datetime] = None
    consumed_at: Optional[datetime] = None
    last_polled_at: Optional[datetime] = None


class User(SQLModel, table=True):
    """Per-user activation state (wave-54 Hour-0).

    Receipt v0 carries identity through `HookToken.user` (an email-string).
    This row exists to dedupe one-shot lifecycle emails (welcome, future
    digests) — it's lazily upserted on the first activation event.
    """
    __tablename__ = "users"

    email: str = Field(primary_key=True, max_length=320)
    welcome_email_sent_at: Optional[datetime] = Field(default=None)
    # Issue #79 — timestamp of the one-time public-share disclosure consent.
    # NULL means "never consented"; any timestamp means "consented at least
    # once". Once set we never clear it — the user can revoke a specific
    # share, but the acknowledgment that they understand what "public"
    # means stays true forever. Previously stored in localStorage + a local
    # ~/.config/yoru/share-confirmed file; moved server-side so it follows
    # the account across browsers and machines.
    share_consent_given_at: Optional[datetime] = Field(default=None)


class PasswordResetToken(SQLModel, table=True):
    """Single-use password-reset token (wave-14 C4; feature-flagged off by default).

    sha256 of the opaque raw token is what's persisted — same discipline as
    `HookToken.token_hash`. Rows are kept (not deleted on use) so replay
    attempts land on a used-at branch and 401 instead of 404.
    """
    __tablename__ = "password_reset_tokens"

    id: str = Field(primary_key=True)
    user_email: str = Field(index=True, max_length=320)
    token_hash: str = Field(index=True, unique=True)
    issued_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    used_at: Optional[datetime] = None


# ---------- Ingestion ----------

class EventIn(SQLModel):
    """Incoming event from a Claude Code hook.

    `user` is optional: when absent, the events router derives it from the
    bearer token (see events_router + deps.get_current_user). Rejected with
    422 when neither is present. Bodies that carry `user` keep the v0
    'user field is trusted' contract for backward compatibility with
    scripts/smoke-us14.sh and any ingestor that doesn't authenticate.

    `kind` is optional: when absent, the events router classifies it from
    `tool`/`tool_name` (Write|Edit|MultiEdit → file_change, else tool_use).
    Closes gap #3 for the real Claude Code hook stdin shape, which carries
    `tool_name` and no `kind`.

    `tool_name` is accepted as a JSON alias for `tool` to match the Claude
    Code hook stdin key verbatim without breaking v0 callers that send `tool`.
    """
    session_id: str
    user: Optional[str] = None
    kind: Optional[EventKind] = None
    ts: Optional[datetime] = None
    tool: Optional[str] = None
    path: Optional[str] = None
    content: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    raw: Optional[dict] = None
    # Phase C — routing context. `cwd` comes from the Claude Code hook
    # payload; `git_remote` / `git_branch` are populated by the receipt.sh
    # hook from `git -C "$cwd"`, cached per session. When present, the
    # server uses them on first event to resolve the session's target org.
    cwd: Optional[str] = None
    git_remote: Optional[str] = None
    git_branch: Optional[str] = None
    # Transcript dedup key (tailer-origin events). When present, the backend
    # skips re-inserting an (session_id, entry_uuid) it already has.
    entry_uuid: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _accept_tool_name_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and "tool_name" in data and "tool" not in data:
            data = {**data, "tool": data["tool_name"]}
            data.pop("tool_name", None)
        return data


class EventsBatchIn(SQLModel):
    events: list[EventIn] = Field(min_length=1, max_length=1000)


class IngestAck(SQLModel):
    accepted: int
    session_ids: list[str]
    flagged_sessions: list[str]


# ---------- Listing ----------

class SessionListItem(SQLModel):
    id: str
    user: str
    agent: str
    started_at: datetime
    ended_at: Optional[datetime]
    tools_count: int
    files_count: int
    tokens_input: int
    tokens_output: int
    cost_usd: float
    flagged: bool
    flags: list[str]
    title: Optional[str] = None
    # Multi-tenant tenant key (M3) — surfaced so the dashboard renders the org
    # badge + guards cross-org in the UI (design 44a3774a §3; contract seam #4).
    org_id: Optional[str] = None
    workspace_id: Optional[str] = None
    # VCS provider slug (github|gitlab|bitbucket|azure) derived from the
    # session's git_remote — the provider only, NEVER the owner/repo (that would
    # leak identity). Lets the dashboard badge/filter by VCS. None when the
    # remote is absent or points at an unrecognized host.
    vcs: Optional[str] = None
    # Opt-in public share flag (#79) — surfaced so the dashboard can render
    # the "Make public" toggle state without a second round trip.
    is_public: bool = False
    # A–F verdict, so the feed/list can lead with the grade without fetching
    # each session's detail. Same compute_score() the detail page uses, so the
    # card and the detail never disagree. None when not yet computable.
    grade: Optional[str] = None


class SessionTotals(SQLModel):
    """Fleet rollup over the FULL filtered + visibility-scoped set — NOT just the
    returned page. The dashboard "Fleet totals" panel must reflect every session
    the caller can see, not the 50 most recent, or it silently undercounts."""
    tool_count: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    flag_count: int = 0            # total red flags across all sessions
    flagged_sessions: int = 0      # sessions with >= 1 flag
    public_sessions: int = 0
    # Raw rule_id → count; the frontend normalizes to user-facing kinds.
    flags_by_kind: dict[str, int] = Field(default_factory=dict)


class SessionListResponse(SQLModel):
    items: list[SessionListItem]
    total: int
    limit: int
    offset: int
    totals: SessionTotals = Field(default_factory=SessionTotals)


# ---------- Activity feed (cross-session event stream) ----------

class ActivityItem(SQLModel):
    """One curated event in the group-scoped activity feed: what an agent DID
    (a tool call, a file edit, an error) — not a session summary. `user`/`agent`
    are the owning session's, so the row reads 'who · which agent — action'."""
    id: int                 # event id
    session_id: str
    ts: datetime
    user: str               # session owner (a group-mate)
    agent: str
    kind: str               # tool_use | file_change | error
    tool: Optional[str] = None
    path: Optional[str] = None
    flags: list[str] = []


class ActivityResponse(SQLModel):
    items: list[ActivityItem]
    limit: int
    offset: int


# ---------- Detail ----------

class EventOut(SQLModel):
    id: int
    ts: datetime
    kind: str
    tool: Optional[str]
    path: Optional[str]
    content: Optional[str]
    tokens_input: int
    tokens_output: int
    cost_usd: float
    flags: list[str]
    # v1 enrichment — computed at serialization time (sessions_router), not persisted.
    duration_ms: Optional[int] = None
    group_key: Optional[str] = None
    # Truncated tool_response preview for the timeline (stdout + stderr + error).
    output: Optional[str] = None
    # Structured tool_input (capped) so the frontend can render per-tool detail
    # views (diff for Edit, command block for Bash, todo list for TodoWrite).
    # Size-capped at serialization to keep the detail response bounded.
    tool_input: Optional[dict] = None


class FileChangedOut(SQLModel):
    """Structured file-change entry for SessionDetail.files_changed.

    Computed at serialization time from Event rows — not persisted. Frontend
    (SessionDetailPage FileChangedRail + marketing SampleReceipt) shows path +
    op chip + additions/deletions counts.
    """
    path: str
    op: str  # "create" | "edit" | "delete"
    additions: int
    deletions: int


class ScoreBreakdown(SQLModel):
    overall: int
    throughput: int
    reliability: int
    safety: int
    grade: str
    breakdown: dict


class SessionDetail(SessionListItem):
    files_changed: list[FileChangedOut]
    tools_called: list[str]
    summary: Optional[str]
    events: list[EventOut]
    score: Optional[ScoreBreakdown] = None


# ---------- Trail export (§4.6) ----------

class TrailSession(SessionListItem):
    """Session envelope for `/sessions/{id}/trail` — SessionDetail minus events."""
    files_changed: list[str]
    tools_called: list[str]
    summary: Optional[str]


class TrailOut(SQLModel):
    session: TrailSession
    events: list[EventOut]
    exported_at: datetime
    schema_version: str = "v0"


# ---------- Summary ----------

class SummaryOut(SQLModel):
    session_id: str
    summary: str
    generated_at: datetime


# ---------- Auth (hook-token mint/list/revoke) ----------

class HookTokenMintIn(SQLModel):
    user: str = Field(min_length=1, max_length=128)
    label: Optional[str] = Field(default=None, max_length=128)


class HookTokenMintOut(SQLModel):
    token: str
    user_id: str
    user: str


class HookTokenListItem(SQLModel):
    id: str
    label: Optional[str]
    identity_label: Optional[str] = None
    token_type: Optional[str] = None
    machine_hostname: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]


# ---------- Device-code pairing (receipt init) ----------

class DeviceCodeStartIn(SQLModel):
    label: Optional[str] = Field(default=None, max_length=128)
    hostname: Optional[str] = Field(default=None, max_length=256)


class DeviceCodeStartOut(SQLModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceCodePollIn(SQLModel):
    device_code: str = Field(min_length=1)


class DeviceCodePollOut(SQLModel):
    status: str  # pending|approved|expired|denied
    token: Optional[str] = None


class DeviceCodeApproveIn(SQLModel):
    user_code: str = Field(min_length=1, max_length=16)


# ---------- Service tokens (Phase B) ----------

class ServiceTokenCreateIn(SQLModel):
    org_id: str = Field(min_length=1)
    label: str = Field(min_length=1, max_length=128)
    scopes: Optional[list[str]] = Field(default=None)
    # M4 (multi-tenant provisioning, design 44a3774a §4). Optional per-dev
    # attribution: when set, the minted token is bound to this dev's email so
    # their ingested events are attributed to them (not a synthetic service
    # identity). The token always carries org_id, so a dev can only write into
    # this org. Omit for a shared org/fleet token.
    user_email: Optional[str] = Field(default=None, max_length=320)


class ServiceTokenCreateOut(SQLModel):
    token: str
    id: str
    org_id: str
    label: str
    created_at: datetime


class ServiceTokenListItem(SQLModel):
    id: str
    org_id: str
    label: Optional[str]
    machine_hostname: Optional[str]
    scopes: Optional[list[str]]
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]
    minted_by_user_id: Optional[str]


# ---------- API keys (long-lived, headless/CI) ----------

class ApiKeyCreateIn(SQLModel):
    label: Optional[str] = Field(default=None, max_length=128)
    scopes: Optional[list[str]] = None  # default ['ingest']; ⊆ API_KEY_SCOPES
    expires_at: Optional[datetime] = None  # optional; must be in the future


class ApiKeyCreateOut(SQLModel):
    key: str  # raw value — returned once, never persisted or logged
    id: str
    key_prefix: str
    label: Optional[str]
    scopes: list[str]
    created_at: datetime
    expires_at: Optional[datetime]


class ApiKeyListItem(SQLModel):
    id: str
    key_prefix: str
    label: Optional[str]
    scopes: list[str]
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]
    expires_at: Optional[datetime]


# ---------- Password reset (wave-14 C4) ----------

class PasswordResetRequestIn(SQLModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetRequestOut(SQLModel):
    sent: bool


class PasswordResetConfirmIn(SQLModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class PasswordResetConfirmOut(SQLModel):
    reset: str


# ---------- Welcome email (wave-54 ACTIVATION Hour-0) ----------

class WelcomeEmailOut(SQLModel):
    sent: bool
    user_email: str
    welcome_email_sent_at: datetime


# ---------- Team dashboard ----------

class TeamDashboardUser(SQLModel):
    email: str
    sessions: int
    flagged: int
    total_cost_usd: float


class TeamDashboardTotals(SQLModel):
    sessions: int
    flagged: int
    flagged_pct: float


class TeamDashboardOut(SQLModel):
    users: list[TeamDashboardUser]
    totals: TeamDashboardTotals


# ---------- Public share (#79) ----------

class ShareIn(SQLModel):
    """Request body for POST /sessions/{id}/share.

    `source` lets the caller identify whether the toggle came from the
    dashboard UI or the CLI (`yoru share`). We count both to measure which
    affordance actually drives the share loop. Defaults to "dashboard" so
    the frontend doesn't have to send it.
    """
    source: Literal["dashboard", "cli"] = "dashboard"


class ShareOut(SQLModel):
    """Response from POST /sessions/{id}/share and /revoke."""
    session_id: str
    is_public: bool
    # Canonical public URL when is_public=true, None when private/revoked.
    public_url: Optional[str] = None


class PublicEventOut(SQLModel):
    """Event shape for unauth GET /public/sessions/{id}.

    Same structural fields as EventOut but `content`, `output`, and
    `tool_input` are stripped when the event carries any `secret_*` flag —
    we preserve the *fact* a secret was flagged (viewers want to see "Claude
    almost pushed an AWS key here") but hide the secret itself.
    """
    id: int
    ts: datetime
    kind: str
    tool: Optional[str]
    path: Optional[str]
    content: Optional[str]
    flags: list[str]
    duration_ms: Optional[int] = None
    group_key: Optional[str] = None
    output: Optional[str] = None
    tool_input: Optional[dict] = None


class ShareConsentOut(SQLModel):
    """Response for GET and POST /api/v1/account/share-consent (#79).

    Callers check `consented` before prompting the user to confirm. `at`
    is the UTC timestamp of the first consent (useful for audit screens
    later; safe to ignore in the UI)."""
    consented: bool
    at: Optional[datetime] = None


class PublicSessionOut(SQLModel):
    """Public-facing session detail for unauth /public/sessions/{id}.

    Differs from SessionDetail by explicit PII redaction:
    - `user` (owner email) is NOT included.
    - `cwd`, `git_remote`, `git_branch` are NOT included — they leak the
      machine's directory layout and the private repo URL.
    - `workspace_id` is NOT included — internal routing id, not useful
      publicly and could enable enumeration.
    - Events with `secret_*` flags have content/output/tool_input stripped
      by the router before serialization.

    Grade, red-flag categories, token aggregates, tool names, and file
    paths remain visible. File paths ARE publicly visible (warned at
    opt-in time) because the redacted replay still needs to show "Claude
    edited src/auth/bearer.ts" to be narrative.
    """
    id: str
    agent: str
    started_at: datetime
    ended_at: Optional[datetime]
    tools_count: int
    files_count: int
    tokens_input: int
    tokens_output: int
    cost_usd: float
    flagged: bool
    flags: list[str]
    title: Optional[str]
    files_changed: list[FileChangedOut]
    tools_called: list[str]
    summary: Optional[str]
    events: list[PublicEventOut]
    score: Optional[ScoreBreakdown] = None


# ---------- Custom red-flag rules (design trovex:961a5e80, task 569f1d47) ---

class CustomRuleIn(SQLModel):
    """Body for POST /orgs/{org_id}/red-flag-rules."""
    name: str
    enabled: bool = True
    kind_filter: Optional[str] = None
    tool_filter: Optional[list[str]] = None
    match_type: str  # "contains" | "path_glob" — validated by custom_rules.validate_rule
    pattern: str
    severity: str  # "critical" | "high" | "medium"


class CustomRuleUpdate(SQLModel):
    """Body for PATCH /orgs/{org_id}/red-flag-rules/{rule_id}. Every field
    optional — omitted fields keep their current value."""
    name: Optional[str] = None
    enabled: Optional[bool] = None
    kind_filter: Optional[str] = None
    tool_filter: Optional[list[str]] = None
    match_type: Optional[str] = None
    pattern: Optional[str] = None
    severity: Optional[str] = None


class CustomRuleOut(SQLModel):
    id: str
    org_id: str
    name: str
    enabled: bool
    kind_filter: Optional[str]
    tool_filter: Optional[list[str]]
    match_type: str
    pattern: str
    severity: str
    created_by: str
    created_at: datetime
    updated_at: datetime
