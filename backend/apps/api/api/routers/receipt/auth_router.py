"""Receipt v0 — hook-token auth router.

Endpoints:
  - POST   /auth/hook-token              mint a new rcpt_* token (unauth in v0)
  - GET    /auth/hook-tokens             list caller's tokens (bearer required)
  - DELETE /auth/hook-token/{id}         soft-revoke caller's token
  - POST   /auth/logout                  revoke bearer (wave-14 C3)
  - POST   /auth/refresh                 rotate refresh token (wave-48 C1)
  - POST   /auth/password-reset-request  issue a reset token (wave-14 C4, flag-gated)
  - POST   /auth/password-reset-confirm  consume a reset token (wave-14 C4, flag-gated)

Contract: vault/BACKEND-API-V0.md §4.6–§4.8, vault/AUTH-V0.md §1(a),
vault/AUTH-HARDENING-V1.md §1/§3/§4/§6.
Token storage: raw token returned once on mint, sha256 hash persisted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import UTC, timedelta

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import update as sa_update
from sqlmodel import Session as DBSession
from sqlmodel import select

from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME
from apps.api.api.services.auth.provider import get_auth_provider
from libs.datastore import get_data_store

from .auth_sessions_model import AuthSession
from .db import get_session
from .deps import _naive_utc_now, deny_api_key_auth, require_current_user
from .email.welcome import send_welcome_email
from .models import (
    API_KEY_SCOPES,
    ApiKey,
    ApiKeyCreateIn,
    ApiKeyCreateOut,
    ApiKeyListItem,
    CliToken,
    DeviceAuthorization,
    DeviceAuthorizationToken,
    DeviceCodeApproveIn,
    DeviceCodePollIn,
    DeviceCodePollOut,
    DeviceCodeStartIn,
    DeviceCodeStartOut,
    HookTokenListItem,
    HookTokenMintIn,
    HookTokenMintOut,
    OrgIdentityItem,
    PasswordResetConfirmIn,
    PasswordResetConfirmOut,
    PasswordResetRequestIn,
    PasswordResetRequestOut,
    PasswordResetToken,
    ServiceTokenCreateIn,
    ServiceTokenCreateOut,
    ServiceTokenListItem,
    User,
    WelcomeEmailOut,
)

# Backward-compat alias — auth_router still references `HookToken` in many
# places (mint + list + revoke). Keep both names resolving to the same model
# until Phase B cleanup pass.
HookToken = CliToken

# Idempotency window for /auth/welcome-email — second call inside this
# window after a successful send is a no-op (returns sent=False with the
# original timestamp). Matches the wave-54 task brief acceptance criteria.
_WELCOME_EMAIL_DEDUPE_WINDOW = timedelta(minutes=5)

_TOKEN_PREFIX = "rcpt_"           # legacy prefix — still accepted on read
_USER_TOKEN_PREFIX = "rcpt_u_"    # Phase B: new user-scoped tokens
_SERVICE_TOKEN_PREFIX = "rcpt_s_" # Phase B: new org/service tokens
_API_KEY_PREFIX = "yoru_ak_"      # Long-lived API keys (headless/CI)
_BEARER_PREFIX = "Bearer "
_RESET_TOKEN_PREFIX = "rcpt_reset_"
_RESET_TOKEN_TTL = timedelta(hours=1)

# Device-code pairing (receipt init) — RFC 8628 inspired.
_DEVICE_CODE_TTL = timedelta(minutes=10)
_DEVICE_POLL_INTERVAL = 2  # seconds
_USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no O/0/I/1 ambiguity


def _gen_user_code() -> str:
    """Generate a human-typeable code like 'ABCD-EFGH' from an unambiguous
    alphabet (no 0/O, 1/I). ~32^8 = 10^12 combinations — the short TTL +
    one-shot approval makes brute-force infeasible."""
    raw = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"

# AUTH-HARDENING-V1 §2 — split access/refresh lifetimes.
_ACCESS_TTL = timedelta(minutes=15)
_REFRESH_TTL = timedelta(days=30)
_JWT_ALGO = "HS256"
_REFRESH_COOKIE = "refresh_token"
_DEV_JWT_SECRET = "dev-only-unsafe-change-me"

_logger = logging.getLogger(__name__)

# Fail fast in production if the JWT secret is missing. Tests and local dev
# fall back to a known-unsafe constant so the suite stays runnable. The
# silent-fallback path in prod was the risk flagged by the pre-beta audit
# (issue #54): a misdeployed container would mint forgeable access JWTs
# without a single log line the operator would notice.
if not os.environ.get("AUTH_JWT_SECRET"):
    _env = os.environ.get("ENVIRONMENT", "").strip().lower()
    if _env in ("prod", "production"):
        raise RuntimeError(
            "AUTH_JWT_SECRET is unset in ENVIRONMENT=" + _env + ". Refusing to "
            "boot with the dev-only fallback — set the secret and redeploy."
        )
    _logger.warning(
        "AUTH_JWT_SECRET unset — access tokens will be signed with the "
        "dev-only fallback secret. Do NOT deploy this way."
    )


def _jwt_secret() -> str:
    return os.environ.get("AUTH_JWT_SECRET") or _DEV_JWT_SECRET


def _is_local_auth() -> bool:
    """True on a self-hosted local deploy (no Supabase). Gated solely on
    AUTH_PROVIDER — same switch as the datastore + auth-provider factories — so
    the cloud (Supabase) path is never reached on local and never altered on
    cloud. Self-host is single-tenant: there is no org/membership model, so the
    org-admin checks below collapse to "any authenticated dashboard user".
    """
    return os.getenv("AUTH_PROVIDER", "local").strip().lower() != "supabase"


def org_default_workspace_id(org_id: str) -> str:
    """Resolve the 'Default' workspace id of an org — existing API contract
    accepts `org_id` but new DB column is `workspace_id`. We look up the
    default workspace row via Supabase PostgREST (RLS will enforce the
    caller's membership).

    Self-host has no Supabase `workspaces` table, so we bind to a
    deterministic per-org local id — create/list/revoke all derive the same
    value, keeping them consistent without any cloud call.

    Module-level (not a method) so events_router can reuse it for the
    default_org_id fallback (DEC-yoru-design-ruling-1 A.3#2) without
    instantiating AuthRouter.
    """
    if _is_local_auth():
        return f"local:{org_id}"
    import httpx
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    anon = os.environ.get("SUPABASE_ANON_KEY", "")
    resp = httpx.get(
        f"{supabase_url}/rest/v1/workspaces",
        headers={"apikey": anon, "Authorization": f"Bearer {anon}"},
        params={"select": "id", "org_id": f"eq.{org_id}", "slug": "eq.default", "deleted_at": "is.null"},
        timeout=5.0,
    )
    resp.raise_for_status()
    rows = resp.json() or []
    if not rows:
        raise HTTPException(status_code=404, detail="organization has no default workspace")
    return rows[0]["id"]


def _org_member_emails(store, org_id: str) -> set[str]:
    """Emails of every `organization_members` row for `org_id`, resolved
    via `profiles` — same two-table join pattern as
    `visibility.py::_co_member_user_ids`/`visible_emails`, just keyed by
    org instead of by co-membership. Used by `list_org_identities`
    (22f98e0a) to turn an org_id into the set of CliToken.user values that
    belong to it (CliToken carries no reliable org_id of its own for
    user-type tokens — see CliToken's own docstring)."""
    rows = store.query_records("organization_members", filters={"org_id": org_id})
    emails: set[str] = set()
    for r in rows:
        uid = r.get("user_id")
        if not uid:
            continue
        prof = store.get_record("profiles", uid)
        if prof and prof.get("email"):
            emails.add(prof["email"])
    return emails


def require_dashboard_jwt(request: Request) -> str:
    """Admin endpoints require a dashboard session (Supabase JWT via cookie).
    CLI bearer tokens are rejected — you manage tokens from the UI, not from
    another CLI. Module-level so other routers (e.g. analytics_router's
    org-wide token-usage gate, 9be89019) can reuse it without instantiating
    AuthRouter."""
    jwt = request.cookies.get(SESSION_COOKIE_NAME)
    if not jwt:
        raise HTTPException(
            status_code=401,
            detail="Sign in to the dashboard to manage service tokens",
        )
    return jwt


def require_org_admin(request: Request, org_id: str) -> str:
    """Authorize an org-admin action. Returns the caller's email.

    One of yoru's two sanctioned authz primitives (DEC-yoru-rbac-ruling-1,
    design 325e07f9, review-backend-api §2) — this one answers "may this
    caller run this org-wide aggregate/admin action at all" (a binary
    gate). The OTHER primitive, `visible_scope_sync`/`visible_emails_sync`
    (`services/access/visibility.py`), answers a different question —
    "which rows can this caller see" (per-row read-scoping). A new
    org-wide/admin route reuses one of these two, never a third hand-rolled
    check.

    Invariant: a `CliToken` bearer can NEVER reach this gate — it goes
    through `require_dashboard_jwt` first, which explicitly rejects CLI
    bearer tokens. `CliToken` carries an identity (which user/machine),
    never a role; a hook token is designed to live in a CI env var or a
    dev's `~/.config`, and must never be able to escalate to admin power by
    itself. Cloud-side, this gate is (dashboard-session) + (org owner/admin
    membership OR studio super-admin) — never (CliToken) + anything.

    Self-host (AUTH_PROVIDER=local): single-tenant, no org model. Per the
    README ("the first registered user becomes the admin"), any
    authenticated dashboard user is authorized — validate the session via
    the local auth provider; authenticated == authorized.

    Cloud (AUTH_PROVIDER=supabase): the caller must be owner or admin of
    `org_id` per `organization_members` — UNLESS they are a studio
    super-admin (`profiles.role == 'admin'`), who bypasses the per-org
    membership check for ANY `org_id` (DEC-yoru-rbac-ruling-1 Q1: consistent
    with `visible_scope_sync`'s super-admin semantics — that caller already
    sees every session across every org via the session read path, so this
    is zero new exposure, just the same admin power reaching this gate too).

    Module-level (not a method) so other routers can reuse it — introduced
    for the token-analytics org-wide gate (9be89019, interim RBAC guard
    ahead of the d2cf7c71 design ruling: a dev must never be able to pull a
    colleague's usage). `AuthRouter._require_org_admin` delegates here.
    """
    jwt = require_dashboard_jwt(request)

    if _is_local_auth():
        email = get_auth_provider().email_from_token(jwt)
        if not email:
            raise HTTPException(status_code=401, detail="Invalid session")
        return email

    from libs.supabase.supabase import SupabaseManager
    supabase = SupabaseManager(access_token=jwt)
    try:
        user_resp = supabase.client.auth.get_user(jwt)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid session") from exc
    if not user_resp or not user_resp.user:
        raise HTTPException(status_code=401, detail="Invalid session")
    caller_id = user_resp.user.id
    caller_email = user_resp.user.email or caller_id

    try:
        profile_rows = supabase.query_records("profiles", filters={"id": caller_id})
    except Exception as exc:
        raise HTTPException(status_code=500, detail="profile lookup failed") from exc
    if profile_rows and profile_rows[0].get("role") == "admin":
        return caller_email

    try:
        memberships = supabase.query_records(
            "organization_members",
            filters={"org_id": org_id, "user_id": caller_id},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="membership lookup failed") from exc
    if not memberships:
        raise HTTPException(
            status_code=403,
            detail="You are not a member of this organization",
        )
    role = memberships[0].get("role")
    if role not in ("owner", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Owner or admin role required for this action",
        )
    return caller_email


def _hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _epoch(dt) -> int:
    """Convert a naive-UTC datetime to an integer epoch second."""
    return int(dt.replace(tzinfo=UTC).timestamp())


# Future-complete email body. Delivery is deferred (wave-14 C4 ships plumbing
# only); wiring Resend/Supabase is tracked in vault/audits/auth-password-reset-design.md.
PASSWORD_RESET_EMAIL_TEMPLATE = """\
Subject: Reset your Receipt password

Hi,

We received a request to reset the password for the Receipt account
tied to this email address. If you didn't ask for this, you can ignore
this message.

To set a new password, open this link within the next hour:

    {reset_link}

The link can be used once. After that, request a new one if you still
need to reset.

— Receipt
"""


def _password_reset_enabled() -> bool:
    """Feature flag for wave-14 C4 reset plumbing.

    Env-driven (mirrors the `RATELIMIT_ENABLED` pattern in
    `api/core/ratelimit.py`), default false. When false, both reset
    endpoints 404 so the route surface is indistinguishable from an
    unknown path (no information leak about the feature's existence).
    """
    v = os.environ.get("AUTH_PASSWORD_RESET_ENABLED", "").strip().lower()
    return v in ("1", "true", "yes", "on")


class AuthRouter:
    """Hook-token lifecycle (mint / list / revoke)."""

    def __init__(self) -> None:
        self.router = APIRouter(prefix="/auth", tags=["receipt:auth"])
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.post(
            "/hook-token",
            response_model=HookTokenMintOut,
            status_code=status.HTTP_201_CREATED,
        )(self.mint_token)
        self.router.get(
            "/hook-tokens",
            response_model=list[HookTokenListItem],
        )(self.list_tokens)
        self.router.delete(
            "/hook-token/{token_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
        )(self.revoke_token)
        self.router.get(
            "/org/identities",
            response_model=list[OrgIdentityItem],
        )(self.list_org_identities)
        self.router.post(
            "/api-keys",
            response_model=ApiKeyCreateOut,
            status_code=status.HTTP_201_CREATED,
        )(self.create_api_key)
        self.router.get(
            "/api-keys",
            response_model=list[ApiKeyListItem],
        )(self.list_api_keys)
        self.router.delete(
            "/api-key/{key_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
        )(self.revoke_api_key)
        self.router.post(
            "/api-key/{key_id}/rotate",
            response_model=ApiKeyCreateOut,
            status_code=status.HTTP_201_CREATED,
        )(self.rotate_api_key)
        # Device-pair flow. Rate limits were attempted via slowapi decorators
        # (issue #54 P1) but the wrapping broke FastAPI body introspection —
        # endpoints started returning 422 "query: Field required" for valid
        # POST bodies in prod. Reverted on 2026-04-24; will re-land via the
        # path-prefix middleware pattern (RateLimitMiddleware) in a follow-up.
        self.router.post(
            "/device-code",
            response_model=DeviceCodeStartOut,
            status_code=status.HTTP_201_CREATED,
        )(self.device_code_start)
        self.router.post(
            "/device-code/poll",
            response_model=DeviceCodePollOut,
        )(self.device_code_poll)
        self.router.post(
            "/device-code/approve",
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
        )(self.device_code_approve)
        self.router.post(
            "/service-token",
            response_model=ServiceTokenCreateOut,
            status_code=status.HTTP_201_CREATED,
        )(self.create_service_token)
        self.router.get(
            "/service-tokens",
            response_model=list[ServiceTokenListItem],
        )(self.list_service_tokens)
        self.router.delete(
            "/service-token/{token_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
        )(self.revoke_service_token)
        self.router.post(
            "/logout",
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
        )(self.logout)
        self.router.post(
            "/refresh",
            response_model=None,
        )(self.refresh)
        self.router.post(
            "/password-reset-request",
            response_model=PasswordResetRequestOut,
            status_code=status.HTTP_202_ACCEPTED,
        )(self.password_reset_request)
        self.router.post(
            "/password-reset-confirm",
            response_model=PasswordResetConfirmOut,
        )(self.password_reset_confirm)
        self.router.post(
            "/welcome-email",
            response_model=WelcomeEmailOut,
        )(self.welcome_email)

    def mint_token(
        self,
        body: HookTokenMintIn,
        request: Request,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> HookTokenMintOut:
        """Create a new hook-token for the authenticated caller. Raw value
        is returned **once**; only the sha256 hash is persisted.

        v1 hardening: the `user` in `body` is IGNORED. Identity is taken
        from the session cookie (Supabase JWT) or bearer. Previously this
        endpoint was unauth and trusted body.user — see wave-XX CVE note.

        API-key callers are refused (403): a leaked key must not mint
        hook-tokens that outlive its revocation.
        """
        deny_api_key_auth(request)
        raw = _USER_TOKEN_PREFIX + secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        row = CliToken(
            id=uuid.uuid4().hex,
            user=current_user,
            token_hash=token_hash,
            token_type="user",
            minted_by_user_id=current_user,
            label=body.label,
            created_at=_naive_utc_now(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return HookTokenMintOut(token=raw, user_id=row.id, user=row.user)

    # ---------- Device-code flow (receipt init) ----------

    def device_code_start(
        self,
        body: DeviceCodeStartIn,
        request: Request,
        db: DBSession = Depends(get_session),
    ) -> DeviceCodeStartOut:
        """CLI entry-point — unauth. Mints a pending device_code / user_code
        pair, TTL 10min. The CLI polls `/device-code/poll` until a user
        approves the pairing from an authenticated browser.
        """
        device_code = secrets.token_urlsafe(32)
        device_code_hash = hashlib.sha256(device_code.encode("utf-8")).hexdigest()

        # Retry on user_code collision — 32^8 space, collisions realistically
        # only happen with concurrent issuance + active rows.
        user_code = ""
        for _ in range(5):
            candidate = _gen_user_code()
            existing = db.exec(
                select(DeviceAuthorization).where(
                    DeviceAuthorization.user_code == candidate,
                    DeviceAuthorization.status == "pending",
                )
            ).first()
            if existing is None:
                user_code = candidate
                break
        if not user_code:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="could not allocate a free user_code — retry",
            )

        now = _naive_utc_now()
        row = DeviceAuthorization(
            id=uuid.uuid4().hex,
            device_code_hash=device_code_hash,
            user_code=user_code,
            status="pending",
            label=body.label,
            hostname=body.hostname,
            created_at=now,
            expires_at=now + _DEVICE_CODE_TTL,
        )
        db.add(row)
        db.commit()

        origin = os.environ.get("FRONTEND_ORIGIN", "").rstrip("/") or str(
            request.base_url
        ).rstrip("/")
        verification_uri = f"{origin}/cli/pair"
        return DeviceCodeStartOut(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=f"{verification_uri}?code={user_code}",
            expires_in=int(_DEVICE_CODE_TTL.total_seconds()),
            interval=_DEVICE_POLL_INTERVAL,
        )

    def device_code_poll(
        self,
        body: DeviceCodePollIn,
        request: Request,
        db: DBSession = Depends(get_session),
    ) -> DeviceCodePollOut:
        """CLI poll — unauth. Returns the minted hook-token the first time
        an approved row is seen, then transitions it to `consumed`.
        """
        device_code_hash = hashlib.sha256(body.device_code.encode("utf-8")).hexdigest()
        row = db.exec(
            select(DeviceAuthorization).where(
                DeviceAuthorization.device_code_hash == device_code_hash,
            )
        ).first()
        if row is None:
            # Don't leak whether the code ever existed — report expired.
            return DeviceCodePollOut(status="expired")

        now = _naive_utc_now()
        row.last_polled_at = now

        if row.expires_at < now and row.status == "pending":
            row.status = "expired"
            db.add(row)
            db.commit()
            return DeviceCodePollOut(status="expired")

        if row.status == "pending":
            db.add(row)
            db.commit()
            return DeviceCodePollOut(status="pending")

        if row.status == "approved":
            # Read-once: reveal the raw token, transition to consumed.
            # The raw lives in the transient `device_authorization_tokens`
            # table keyed by device_code_hash (written in /approve). First
            # successful poll reads it, stamps the real sha256 on the
            # pairing row, deletes the transient row, transitions to
            # consumed. Later polls find no transient row → denied.
            transient = db.get(DeviceAuthorizationToken, device_code_hash)
            if transient is not None and transient.expires_at >= now:
                raw_token = transient.raw_token
                row.token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
                row.status = "consumed"
                row.consumed_at = now
                db.delete(transient)
                db.add(row)
                db.commit()
                return DeviceCodePollOut(status="approved", token=raw_token, identity_id=row.cli_token_id)
            # Transient expired or already consumed by an earlier poll —
            # the CLI stops polling (it either got its token once, or the
            # pairing rotted past TTL). Safer to report denied than to
            # leak whether the raw ever existed.
            return DeviceCodePollOut(status="denied")

        # consumed / denied / expired
        return DeviceCodePollOut(status=row.status)

    def device_code_approve(
        self,
        body: DeviceCodeApproveIn,
        request: Request,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
        x_organization_id: str | None = Header(default=None, alias="X-Organization-Id"),
    ) -> JSONResponse:
        """Browser endpoint — authed (cookie + CSRF). User types `user_code`,
        confirms, and we bind the pending row to their identity + mint the
        hook-token. Token raw value is NOT returned here — the CLI picks it
        up on its next poll.

        `X-Organization-Id` (same header convention as
        `custom_rules_router.py`), when the browser session sent one, seeds
        `CliToken.default_org_id` — the A.3#2/89fd589d fallback that was
        shipped inert until now (nothing ever populated it). Trusted as-is,
        no membership check: this only ever affects the CALLER'S OWN future
        unrouted events' `workspace_id` (an additive routing fallback,
        never `Session.org_id` — the actual cross-tenant read wall, which
        `default_org_id` never touches). Absent (self-host, or a frontend
        that hasn't wired the header yet) → stays `None`, unchanged
        behavior from before this ticket.
        """
        row = db.exec(
            select(DeviceAuthorization).where(
                DeviceAuthorization.user_code == body.user_code.strip().upper(),
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="unknown pairing code",
            )

        now = _naive_utc_now()
        if row.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"code is {row.status}",
            )
        if row.expires_at < now:
            row.status = "expired"
            db.add(row)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="code has expired — re-run `receipt init`",
            )

        raw_token = _USER_TOKEN_PREFIX + secrets.token_urlsafe(24)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        hook_row = CliToken(
            id=uuid.uuid4().hex,
            user=current_user,
            token_hash=token_hash,
            token_type="user",
            minted_by_user_id=current_user,
            label=row.label or "cli-pair",
            identity_label=row.label or "cli-pair",
            machine_hostname=row.hostname,
            created_at=now,
            default_org_id=x_organization_id,
        )
        db.add(hook_row)

        # The raw token is the CLI's one-time prize, read once on its next
        # /poll. It lives in a dedicated transient table keyed by
        # device_code_hash for that brief window (seconds), then deleted.
        # (Event scoping to orgs is resolved server-side via route_rules at
        # ingest time — the token itself has no scope.)
        transient = DeviceAuthorizationToken(
            device_code_hash=row.device_code_hash,
            raw_token=raw_token,
            expires_at=row.expires_at,
        )
        db.add(transient)
        row.status = "approved"
        row.user = current_user
        row.token_hash = token_hash
        row.cli_token_id = hook_row.id
        row.approved_at = now
        db.add(row)
        db.commit()

        _logger.info(
            "device_code.approved user=%s label=%s user_code=%s",
            current_user, row.label, body.user_code,
        )
        return JSONResponse(status_code=status.HTTP_204_NO_CONTENT, content=None)

    def list_tokens(
        self,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> list[HookTokenListItem]:
        """List the caller's USER tokens (not service tokens — those live
        under /auth/service-tokens, scoped to an org and admin-managed)."""
        rows = db.exec(
            select(CliToken)
            .where(
                CliToken.user == current_user,
                CliToken.token_type != "service",
            )
            .order_by(CliToken.created_at.desc())
        ).all()
        return [HookTokenListItem.model_validate(r.model_dump()) for r in rows]

    def list_org_identities(
        self,
        request: Request,
        org_id: str = Query(...),
        db: DBSession = Depends(get_session),
    ) -> list[OrgIdentityItem]:
        """Org-wide connected-identity list for the CTO view (22f98e0a).

        Role-gated via `require_org_admin` — self-host: any authenticated
        dashboard user; cloud: org owner/admin, or a studio super-admin
        (DEC-yoru-rbac-ruling-1 Q1). Reuses the existing gate verbatim, no
        new authz primitive. Never satisfiable via a CliToken bearer —
        `require_org_admin` → `require_dashboard_jwt` reads the dashboard
        session cookie exclusively, same invariant as 9be89019/5a72353b.
        """
        require_org_admin(request, org_id)

        if _is_local_auth():
            # Self-host is single-tenant — there is no real org model, so
            # "the org" is the whole instance (the same posture
            # require_org_admin itself already takes here). Every user-type
            # identity in the local DB counts as "in this org"; CliToken.org_id
            # is NULL for user tokens today regardless (see CliToken's own
            # docstring), so filtering on it would just return nothing.
            member_emails: Optional[set[str]] = None
        else:
            member_emails = _org_member_emails(get_data_store(), org_id)
            if not member_emails:
                return []

        stmt = select(CliToken).where(CliToken.token_type != "service")
        if member_emails is not None:
            stmt = stmt.where(CliToken.user.in_(member_emails))
        stmt = stmt.order_by(CliToken.created_at.desc())
        rows = db.exec(stmt).all()

        now = _naive_utc_now()
        items: list[OrgIdentityItem] = []
        for r in rows:
            if r.revoked_at is not None:
                row_status = "revoked"
            elif r.expires_at is not None and r.expires_at < now:
                row_status = "expired"
            else:
                row_status = "active"
            items.append(OrgIdentityItem(
                org_id=org_id,
                user=r.user,
                id=r.id,
                identity_label=r.identity_label,
                machine_hostname=r.machine_hostname,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                status=row_status,
            ))
        return items

    def revoke_token(
        self,
        token_id: str,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> None:
        """Soft-revoke: set `revoked_at = now()`.

        Per AUTH-V0 §1(a): 401 when the token belongs to a different user
        (not 404 — spec is explicit), 404 when token_id is unknown,
        idempotent 204 when already revoked.
        """
        row = db.get(HookToken, token_id)
        if row is None:
            raise HTTPException(status_code=404, detail="token not found")
        if row.user != current_user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="token does not belong to caller",
            )
        if row.revoked_at is None:
            row.revoked_at = _naive_utc_now()
            db.add(row)
            db.commit()
        return None

    # ---------- API keys (long-lived, headless/CI) ----------

    @staticmethod
    def _mint_api_key_row(
        user: str,
        label: str | None,
        scopes: list[str],
        expires_at: object,
    ) -> tuple[str, ApiKey]:
        """Mint (raw_value, unsaved row). Shared by create + rotate."""
        raw = _API_KEY_PREFIX + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key_prefix = raw[len(_API_KEY_PREFIX):len(_API_KEY_PREFIX) + 8]
        row = ApiKey(
            id=uuid.uuid4().hex,
            user=user,
            key_hash=key_hash,
            key_prefix=key_prefix,
            label=label,
            scopes=json.dumps(sorted(scopes)),
            created_at=_naive_utc_now(),
            expires_at=expires_at,
        )
        return raw, row

    @staticmethod
    def _api_key_out(raw: str, row: ApiKey) -> ApiKeyCreateOut:
        return ApiKeyCreateOut(
            key=raw,
            id=row.id,
            key_prefix=row.key_prefix,
            label=row.label,
            scopes=json.loads(row.scopes),
            created_at=row.created_at,
            expires_at=row.expires_at,
        )

    def create_api_key(
        self,
        body: ApiKeyCreateIn,
        request: Request,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> ApiKeyCreateOut:
        """Create a long-lived API key for the authenticated caller. The raw
        value is returned **once**; only the sha256 hash is persisted. Store
        it in a secrets manager or env var — it is a full bearer credential
        and must never be logged or committed.

        Scopes default to `['ingest']` (POST /sessions/events only) — the
        least privilege a CI runner needs. Add `'read'` explicitly for
        programmatic reads.

        API-key callers are refused (403) so a leaked key can't mint
        replacements for itself.
        """
        deny_api_key_auth(request)

        scopes = body.scopes if body.scopes is not None else ["ingest"]
        bad = set(scopes) - API_KEY_SCOPES
        if bad or not scopes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"scopes must be a non-empty subset of "
                       f"{sorted(API_KEY_SCOPES)}",
            )

        expires_at = body.expires_at
        if expires_at is not None:
            if expires_at.tzinfo is not None:
                expires_at = expires_at.astimezone(UTC).replace(tzinfo=None)
            if expires_at <= _naive_utc_now():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="expires_at must be in the future",
                )

        raw, row = self._mint_api_key_row(
            current_user, body.label, scopes, expires_at
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return self._api_key_out(raw, row)

    def list_api_keys(
        self,
        request: Request,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> list[ApiKeyListItem]:
        """List the caller's API keys (prefixes only — raw values are never
        stored, so they can't be shown again)."""
        deny_api_key_auth(request)
        rows = db.exec(
            select(ApiKey)
            .where(ApiKey.user == current_user)
            .order_by(ApiKey.created_at.desc())
        ).all()
        return [
            ApiKeyListItem(
                id=r.id,
                key_prefix=r.key_prefix,
                label=r.label,
                scopes=json.loads(r.scopes) if r.scopes else [],
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                revoked_at=r.revoked_at,
                expires_at=r.expires_at,
            )
            for r in rows
        ]

    def _get_owned_api_key(
        self, db: DBSession, key_id: str, current_user: str
    ) -> ApiKey:
        """Fetch an API key owned by the caller or raise 404.

        Deliberate divergence from the hook-token contract (AUTH-V0 §1(a)
        returns 401 for a foreign token): foreign API keys 404 so an
        authenticated user can't probe whether someone else's key id exists.
        The hook-token endpoint keeps its frozen spec.
        """
        row = db.get(ApiKey, key_id)
        if row is None or row.user != current_user:
            raise HTTPException(status_code=404, detail="API key not found")
        return row

    def revoke_api_key(
        self,
        key_id: str,
        request: Request,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> None:
        """Soft-revoke an API key: set `revoked_at = now()`.

        404 when key_id is unknown OR belongs to another user (no existence
        oracle — see `_get_owned_api_key`), idempotent 204 when already
        revoked.
        """
        deny_api_key_auth(request)
        row = self._get_owned_api_key(db, key_id, current_user)
        if row.revoked_at is None:
            row.revoked_at = _naive_utc_now()
            db.add(row)
            db.commit()
        return None

    def rotate_api_key(
        self,
        key_id: str,
        request: Request,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> ApiKeyCreateOut:
        """Rotate an API key: mint a replacement carrying the same label,
        scopes and absolute expiry, and revoke the old key — one transaction,
        so there is no window where neither key works. Returns the new raw
        value **once**.

        409 when the key is already revoked (nothing to rotate — create a
        new one instead). 404 unknown/foreign, like revoke.
        """
        deny_api_key_auth(request)
        row = self._get_owned_api_key(db, key_id, current_user)
        if row.revoked_at is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="API key is already revoked — create a new one",
            )

        raw, new_row = self._mint_api_key_row(
            current_user,
            row.label,
            json.loads(row.scopes) if row.scopes else ["ingest"],
            row.expires_at,
        )
        row.revoked_at = _naive_utc_now()
        db.add(row)
        db.add(new_row)
        db.commit()
        db.refresh(new_row)
        return self._api_key_out(raw, new_row)

    # ---------- Service tokens (Phase B — headless/CI/fleet) ----------

    def _require_dashboard_jwt(self, request: Request) -> str:
        return require_dashboard_jwt(request)

    def _require_org_admin(self, request: Request, org_id: str) -> str:
        return require_org_admin(request, org_id)

    def _org_default_workspace_id(self, org_id: str) -> str:
        return org_default_workspace_id(org_id)

    def create_service_token(
        self,
        body: ServiceTokenCreateIn,
        request: Request,
        db: DBSession = Depends(get_session),
    ) -> ServiceTokenCreateOut:
        """Mint a long-lived token bound to an organization's default workspace.
        Admin-only. Raw token returned **once**. Phase W7 will refactor this
        to take a `workspace_id` directly.
        """
        caller_email = self._require_org_admin(request, body.org_id)
        workspace_id = self._org_default_workspace_id(body.org_id)

        raw = _SERVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        scopes_json = json.dumps(body.scopes or ["events:write"])
        now = _naive_utc_now()
        # M4: bind the TENANT org_id so ingest via this token stamps
        # sessions.org_id = this org (M2a). Optionally attribute to a specific
        # dev (per-dev provisioning); otherwise a synthetic org-fleet identity.
        row = CliToken(
            id=uuid.uuid4().hex,
            user=body.user_email or f"service:{body.org_id}",
            token_hash=token_hash,
            token_type="service",
            org_id=body.org_id,
            workspace_id=workspace_id,
            minted_by_user_id=caller_email,
            label=body.label,
            scopes=scopes_json,
            created_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        _logger.info(
            "service_token.created org=%s ws=%s user=%s by=%s label=%s",
            body.org_id, workspace_id, row.user, caller_email, body.label,
        )
        return ServiceTokenCreateOut(
            token=raw,
            id=row.id,
            org_id=body.org_id,
            label=row.label or "",
            created_at=row.created_at,
        )

    def list_service_tokens(
        self,
        org_id: str,
        request: Request,
        db: DBSession = Depends(get_session),
    ) -> list[ServiceTokenListItem]:
        """List service tokens for an org (admin-only). Never leaks `token_hash`."""
        self._require_org_admin(request, org_id)
        workspace_id = self._org_default_workspace_id(org_id)
        rows = db.exec(
            select(CliToken)
            .where(CliToken.token_type == "service", CliToken.workspace_id == workspace_id)
            .order_by(CliToken.created_at.desc())
        ).all()
        items: list[ServiceTokenListItem] = []
        for r in rows:
            try:
                scopes = json.loads(r.scopes) if r.scopes else None
            except Exception:
                scopes = None
            items.append(ServiceTokenListItem(
                id=r.id,
                org_id=org_id,
                label=r.label,
                machine_hostname=r.machine_hostname,
                scopes=scopes,
                created_at=r.created_at,
                last_used_at=r.last_used_at,
                revoked_at=r.revoked_at,
                minted_by_user_id=r.minted_by_user_id,
            ))
        return items

    def revoke_service_token(
        self,
        token_id: str,
        request: Request,
        db: DBSession = Depends(get_session),
    ) -> None:
        row = db.get(CliToken, token_id)
        if row is None or row.token_type != "service" or not row.workspace_id:
            raise HTTPException(status_code=404, detail="service token not found")

        if _is_local_auth():
            # Self-host: workspace_id is "local:<org_id>"; authorize any
            # authenticated dashboard user (single-tenant admin). No Supabase.
            org_id = row.workspace_id.removeprefix("local:")
            self._require_org_admin(request, org_id)
            if row.revoked_at is None:
                row.revoked_at = _naive_utc_now()
                db.add(row)
                db.commit()
            return None

        # Look up the org for this workspace so we can admin-check.
        import httpx
        supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
        anon = os.environ.get("SUPABASE_ANON_KEY", "")
        ws_resp = httpx.get(
            f"{supabase_url}/rest/v1/workspaces",
            headers={"apikey": anon, "Authorization": f"Bearer {anon}"},
            params={"select": "org_id,owner_user_id", "id": f"eq.{row.workspace_id}"},
            timeout=5.0,
        )
        ws_resp.raise_for_status()
        ws_rows = ws_resp.json() or []
        if not ws_rows:
            raise HTTPException(status_code=404, detail="workspace not found")
        org_id = ws_rows[0].get("org_id")
        if org_id:
            self._require_org_admin(request, org_id)
        # If the service token is scoped to a personal workspace (future Phase G),
        # we'd gate on ownership — not a current flow.
        if row.revoked_at is None:
            row.revoked_at = _naive_utc_now()
            db.add(row)
            db.commit()
        return None

    def logout(
        self,
        authorization: str | None = Header(default=None),
        db: DBSession = Depends(get_session),
    ) -> None:
        """Revoke the bearer in the Authorization header.

        Stamps `revoked_at` on the matching `hook_tokens` row. 401 when the
        header is missing, malformed, or the token is unknown / already
        revoked — matches AUTH-V0 posture and prevents replay after logout.
        """
        if authorization is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authorization required",
            )
        if not authorization.startswith(_BEARER_PREFIX):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid authorization scheme",
            )
        token = authorization[len(_BEARER_PREFIX):].strip()
        if not token.startswith(_TOKEN_PREFIX):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid token format",
            )
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = db.exec(
            select(HookToken).where(
                HookToken.token_hash == token_hash,
                HookToken.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or revoked token",
            )
        row.revoked_at = _naive_utc_now()
        db.add(row)
        db.commit()
        return None

    async def refresh(
        self,
        request: Request,
        db: DBSession = Depends(get_session),
    ):
        """Rotate a refresh token (AUTH-HARDENING-V1 §3/§4).

        Cookie first, JSON body (`{"refresh_token": ...}`) second.
        - unknown hash             -> 401 `{"error": "auth_unknown"}`
        - expired-not-revoked      -> 401 `{"error": "auth_expired"}` (family untouched)
        - revoked (REUSE)          -> 401 `{"error": "auth_revoked"}` + revoke every
                                      live sibling in the family + WARN log
        - valid                    -> 200 with new access JWT + new refresh token,
                                      refresh cookie rotated, old row stamped
                                      `last_used_at` then `revoked_at`
        """
        raw = request.cookies.get(_REFRESH_COOKIE)
        if not raw:
            try:
                body = await request.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                val = body.get("refresh_token")
                if isinstance(val, str) and val:
                    raw = val

        if not raw:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "auth_unknown"},
            )

        presented_hash = _hash_refresh(raw)
        row = db.exec(
            select(AuthSession).where(
                AuthSession.refresh_token_hash == presented_hash
            )
        ).first()
        if row is None:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "auth_unknown"},
            )

        ip = request.client.host if request.client else None
        ua = (request.headers.get("user-agent") or "")[:256]

        # §4 reuse detection — presented a row that was already consumed.
        if row.revoked_at is not None:
            family_id = row.family_id
            user_email = row.user_email
            now = _naive_utc_now()
            db.execute(
                sa_update(AuthSession)
                .where(AuthSession.family_id == family_id)
                .where(AuthSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            db.commit()
            _logger.warning(
                "event=refresh_family_reuse family_id=%s user_email=%s ip=%s ua=%s",
                family_id, user_email, ip, ua,
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "auth_revoked"},
            )

        now = _naive_utc_now()
        if row.expires_at <= now:
            # Stale cookie — do NOT revoke the family (§4 edge case).
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "auth_expired"},
            )

        # §3 rotation-on-use: mint new token + row, stamp old row, single txn.
        new_raw = secrets.token_urlsafe(32)
        new_hash = _hash_refresh(new_raw)
        new_id = uuid.uuid4().hex
        new_row = AuthSession(
            id=new_id,
            user_email=row.user_email,
            refresh_token_hash=new_hash,
            issued_at=now,
            expires_at=now + _REFRESH_TTL,
            family_id=row.family_id,
            parent_token_hash=presented_hash,
            ip=ip,
            user_agent=ua,
        )
        row.last_used_at = now
        row.revoked_at = now
        db.add(row)
        db.add(new_row)
        db.commit()

        access_exp = now + _ACCESS_TTL
        access_token = jwt.encode(
            {
                "sub": row.user_email,
                "sid": new_id,
                "iat": _epoch(now),
                "exp": _epoch(access_exp),
            },
            _jwt_secret(),
            algorithm=_JWT_ALGO,
        )

        response = JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "access_token": access_token,
                "refresh_token": new_raw,
                "expires_in": int(_ACCESS_TTL.total_seconds()),
                "token_type": "bearer",
            },
        )
        response.set_cookie(
            key=_REFRESH_COOKIE,
            value=new_raw,
            max_age=int(_REFRESH_TTL.total_seconds()),
            httponly=True,
            samesite="lax",
            secure=False,  # dev — TLS termination handles secure=True in prod
        )
        return response

    def password_reset_request(
        self,
        body: PasswordResetRequestIn,
        db: DBSession = Depends(get_session),
    ) -> PasswordResetRequestOut:
        """Issue a single-use, 1-hour reset token for `body.email`.

        Flag-gated: when `AUTH_PASSWORD_RESET_ENABLED` is unset/false, 404 so
        the endpoint is indistinguishable from an unknown path. When on, the
        raw token is logged to stderr with a `[DEV-ONLY …]` prefix (email
        delivery is out of scope for wave-14 — see the design note at
        vault/audits/auth-password-reset-design.md). Only the sha256 hash of
        the token is persisted.
        """
        if not _password_reset_enabled():
            raise HTTPException(status_code=404, detail="Not Found")

        raw = _RESET_TOKEN_PREFIX + secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        now = _naive_utc_now()
        row = PasswordResetToken(
            id=uuid.uuid4().hex,
            user_email=body.email,
            token_hash=token_hash,
            issued_at=now,
            expires_at=now + _RESET_TOKEN_TTL,
        )
        db.add(row)
        db.commit()

        # Dev-only surface so local testing works without an SMTP pipeline.
        # Replace with real delivery once the follow-up ticket lands (see
        # vault/audits/auth-password-reset-design.md §Follow-up).
        print(
            f"[DEV-ONLY password-reset link] email={body.email} token={raw}",
            flush=True,
        )
        return PasswordResetRequestOut(sent=True)

    def welcome_email(
        self,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> WelcomeEmailOut:
        """Fire the welcome / install-snippet email for the caller.

        Idempotent: the second call within `_WELCOME_EMAIL_DEDUPE_WINDOW`
        returns `sent=False` with the original `welcome_email_sent_at` and
        does NOT re-send. Lazily upserts the `users` row keyed by email.

        The frontend calls this once after first sign-in (Supabase magic-link
        provides email → bearer; bearer scopes the call to the caller, so no
        cross-user concern). Stub vs SMTP delivery is decided inside
        `send_welcome_email` based on `SMTP_HOST` env.
        """
        now = _naive_utc_now()
        row = db.get(User, current_user)
        if (
            row is not None
            and row.welcome_email_sent_at is not None
            and (now - row.welcome_email_sent_at) < _WELCOME_EMAIL_DEDUPE_WINDOW
        ):
            return WelcomeEmailOut(
                sent=False,
                user_email=current_user,
                welcome_email_sent_at=row.welcome_email_sent_at,
            )

        send_welcome_email(current_user)

        if row is None:
            row = User(email=current_user, welcome_email_sent_at=now)
        else:
            row.welcome_email_sent_at = now
        db.add(row)
        db.commit()
        return WelcomeEmailOut(
            sent=True,
            user_email=current_user,
            welcome_email_sent_at=now,
        )

    def password_reset_confirm(
        self,
        body: PasswordResetConfirmIn,
        db: DBSession = Depends(get_session),
    ) -> PasswordResetConfirmOut:
        """Consume a reset token and mark it used.

        Flag-gated identically to request. 401 on expired / already-used /
        unknown-hash. On success the token is stamped `used_at = now()` and
        we return a known-string body that flags the incomplete storage path
        (Receipt has no password column on users today — follow-up ticket).
        """
        if not _password_reset_enabled():
            raise HTTPException(status_code=404, detail="Not Found")

        token_hash = hashlib.sha256(body.token.encode("utf-8")).hexdigest()
        now = _naive_utc_now()
        row = db.exec(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
            )
        ).first()
        if row is None or row.used_at is not None or row.expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or expired reset token",
            )

        row.used_at = now
        db.add(row)
        db.commit()

        # TODO(wave-follow-up): once the user model grows a `password_hash`
        # column, hash `body.new_password` (argon2id — product-lead to confirm)
        # and persist here. Tracked in vault/audits/auth-password-reset-design.md.
        _logger.warning(
            "password-reset confirm accepted but password storage not yet "
            "implemented (user_email=%s)",
            row.user_email,
        )
        return PasswordResetConfirmOut(
            reset="accepted-but-password-storage-not-implemented",
        )
