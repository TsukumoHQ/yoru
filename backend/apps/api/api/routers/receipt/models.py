"""SQLModel tables + request/response schemas for Receipt v0.

Single source of truth for all Receipt shapes. Routers import from here.
Contract frozen in vault/BACKEND-API-V0.md §2–§3.
"""
from __future__ import annotations

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


# ---------- Tables ----------

class Session(SQLModel, table=True):
    """Denormalized per-agent-session row."""
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    user: str = Field(index=True)
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


class Event(SQLModel, table=True):
    """Append-only event stream keyed by session."""
    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, foreign_key="sessions.id")
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
    # Stable per-event dedup key from the source transcript (line uuid + block
    # index). Lets the tailer re-read a transcript idempotently — the backend
    # skips an (session_id, entry_uuid) it already has — so events survive
    # backend downtime without ever double-inserting.
    entry_uuid: Optional[str] = Field(default=None, index=True)


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
    workspace_id: Optional[str] = Field(default=None, index=True)  # set if service — target workspace for fleet tokens
    minted_by_user_id: Optional[str] = Field(default=None)  # audit trail
    machine_hostname: Optional[str] = Field(default=None, max_length=256)
    label: Optional[str] = Field(default=None, max_length=128)
    scopes: Optional[str] = Field(default=None)  # JSON: ['events:write', ...]
    created_at: datetime = Field(default_factory=_utcnow)
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


# Back-compat alias so existing call sites (deps, auth_router) keep working
# during the Phase B rollout. New code should import CliToken.
HookToken = CliToken


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
    workspace_id: Optional[str] = None
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
    token_type: Optional[str] = None
    machine_hostname: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]


# ---------- Device-code pairing (receipt init) ----------

class DeviceCodeStartIn(SQLModel):
    label: Optional[str] = Field(default=None, max_length=128)


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
