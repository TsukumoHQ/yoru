"""Shared FastAPI dependencies for Receipt v0 routers.

`get_current_user` resolves a `rcpt_<...>` bearer token to the bound user
string by looking up the sha256 hash in `hook_tokens` (revoked_at IS NULL).

Behavior:
- header absent                  -> return None (backward-compat; events
                                    router falls back to `EventIn.user`)
- header present but malformed   -> 401
- header present, token unknown
  or revoked                     -> 401
- header present, token valid    -> return the bound `user` string

`require_current_user` is the strict variant — raises 401 even when the
header is absent. Not wired in v0 but exported for future routes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlmodel import Session as DBSession, select

from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME

from .db import get_session
from .models import ApiKey, HookToken

_BEARER_PREFIX = "Bearer "
_TOKEN_PREFIX = "rcpt_"  # Matches both legacy `rcpt_*` and new `rcpt_u_*` / `rcpt_s_*`.
_API_KEY_PREFIX = "yoru_ak_"  # Long-lived API keys (headless/CI) — see models.ApiKey.
_API_KEY_HEADER = "X-API-Key"
# `last_used_at` is refreshed at most once per this window so the hot ingest
# path doesn't pay a DB write per request.
_API_KEY_LAST_USED_THROTTLE = timedelta(seconds=60)


def _naive_utc_now() -> datetime:
    """Naive UTC — SQLite drops tzinfo so everything persisted is naive
    (self-learning: SQLite drops tzinfo gotcha)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_token(authorization: str, session: DBSession) -> str:
    """Parse 'Bearer rcpt_...' and return the bound user or raise 401.

    Side-effect: updates `last_used_at` (naive UTC) on the matched row so
    `GET /auth/hook-tokens` can show a freshness indicator.
    """
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
    row = session.exec(
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
    row.last_used_at = _naive_utc_now()
    session.add(row)
    session.commit()
    return row.user


def _required_api_key_scope(request: Request) -> str | None:
    """Map the request to the scope an API key must carry, or None when no
    scope grants this action to API keys at all.

      - POST on the ingest endpoint → 'ingest'
      - GET/HEAD anywhere on the surface that resolves these deps → 'read'
      - everything else → None (mutating verbs are session/bearer-only; this
        structurally locks API keys out of credential minting, sharing, and
        any future mutation added to the surface)
    """
    method = request.method.upper()
    if method == "POST" and request.url.path.rstrip("/").endswith("/sessions/events"):
        return "ingest"
    if method in ("GET", "HEAD"):
        return "read"
    return None


def _resolve_api_key(api_key: str, request: Request, session: DBSession) -> str:
    """Resolve an `X-API-Key: yoru_ak_...` header to the bound user or raise
    401 (bad/revoked/expired key) / 403 (valid key, insufficient scope).

    Marks the request as API-key-authenticated (`request.state.auth_method`)
    so credential-minting endpoints can refuse this vector via
    `deny_api_key_auth` — a leaked key must not be able to mint replacement
    credentials that outlive its own revocation.
    """
    if not api_key.startswith(_API_KEY_PREFIX):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid API key format",
        )

    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    row = session.exec(
        select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or revoked API key",
        )

    now = _naive_utc_now()
    if row.expires_at is not None and row.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has expired",
        )

    request.state.auth_method = "api_key"

    required = _required_api_key_scope(request)
    try:
        granted = set(json.loads(row.scopes)) if row.scopes else set()
    except ValueError:
        granted = set()
    if required is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys cannot perform this action — "
                   "use a dashboard session or CLI token",
        )
    if required not in granted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API key lacks the '{required}' scope",
        )

    if (
        row.last_used_at is None
        or now - row.last_used_at >= _API_KEY_LAST_USED_THROTTLE
    ):
        row.last_used_at = now
        session.add(row)
        session.commit()
    return row.user


def deny_api_key_auth(request: Request) -> None:
    """Guard for credential-minting endpoints (hook-token mint, API-key
    create/list/revoke): refuse API-key-authenticated callers with 403.

    Without this, a leaked API key could mint hook-tokens or more API keys
    and survive its own revocation.
    """
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API keys cannot manage credentials — "
                   "use a dashboard session or CLI token",
        )


def _resolve_from_cookie(request: Request) -> str | None:
    """Resolve the dashboard session cookie (`rcpt_session`, Supabase JWT) to a
    user email string. Returns None if no cookie is present; raises 401 if the
    cookie is present but invalid.

    This is the dashboard auth path — cookies set by `/auth/signin` in
    `routers/auth/cookie_router.py`. CLI tools keep using the `rcpt_*` bearer
    flow via `_resolve_token` above.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    try:
        from apps.api.api.services.auth.provider import get_auth_provider
        email = get_auth_provider().email_from_token(token)
    except Exception:
        email = None
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired session cookie",
        )
    return email


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias=_API_KEY_HEADER),
    session: DBSession = Depends(get_session),
) -> str | None:
    """Optional auth — returns the user email or None when no creds.

    Resolution order:
      1. `Authorization: Bearer rcpt_*`  (CLI hook-token flow)
      2. `X-API-Key: yoru_ak_*` (long-lived API key, headless/CI flow)
      3. `rcpt_session` cookie (Supabase JWT, dashboard flow)
      4. None (v0 backward-compat for ingest fallback to `EventIn.user`)
    """
    if authorization is not None:
        return _resolve_token(authorization, session)
    if x_api_key is not None:
        return _resolve_api_key(x_api_key, request, session)
    return _resolve_from_cookie(request)


def get_current_org(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias=_API_KEY_HEADER),
    session: DBSession = Depends(get_session),
) -> str | None:
    """Resolve the tenant org_id bound to the caller's credential (M2, design
    44a3774a §4), or None when the credential carries no org (legacy token, or
    a dashboard cookie).

    Ingest stamps ``sessions.org_id`` from this, falling back to
    ``DEFAULT_ORG_ID`` when None — so a client-org's devs can only write into
    their own org. Bearer/API-key only; the cookie path returns None (dashboard
    org selection is resolved by membership, not the token).
    """
    if authorization is not None:
        if not authorization.startswith(_BEARER_PREFIX):
            return None
        token = authorization[len(_BEARER_PREFIX):].strip()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        row = session.exec(
            select(HookToken).where(
                HookToken.token_hash == token_hash,
                HookToken.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).first()
        return row.org_id if row is not None else None
    if x_api_key is not None:
        key_hash = hashlib.sha256(x_api_key.encode("utf-8")).hexdigest()
        row = session.exec(
            select(ApiKey).where(
                ApiKey.key_hash == key_hash,
                ApiKey.revoked_at.is_(None),  # type: ignore[union-attr]
            )
        ).first()
        return row.org_id if row is not None else None
    return None


def require_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias=_API_KEY_HEADER),
    session: DBSession = Depends(get_session),
) -> str:
    """Strict auth — 401 unless bearer header, API key, or session cookie
    resolves."""
    if authorization is not None:
        return _resolve_token(authorization, session)
    if x_api_key is not None:
        return _resolve_api_key(x_api_key, request, session)
    user = _resolve_from_cookie(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authorization required",
        )
    return user
