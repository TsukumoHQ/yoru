"""Cookie-based dashboard auth — token never reaches JavaScript.

Separate from the CLI hook-token router (`routers/receipt/auth_router.py`),
which continues to serve `Authorization: Bearer rcpt_*` for CLI tools that
can't speak cookies.

Endpoints (mounted at `/api/v1/auth`):

    POST /signup           → creates user, sets cookies, returns {user}
    POST /signin           → validates password, sets cookies, returns {user}
    POST /session/refresh  → rotates cookies using refresh-token cookie
    POST /session/signout  → clears cookies
    GET  /session/me       → returns current user (cookie-protected)

Cookies:
    rcpt_session   — Supabase access-token JWT (HttpOnly, short TTL)
    rcpt_refresh   — Supabase refresh token (HttpOnly, long TTL, scoped path)
    rcpt_csrf      — CSRF double-submit token (NOT HttpOnly; JS reads and
                     echoes back as `X-CSRF-Token` on mutating requests)

CSRF protection for state-changing requests lives in `middleware/csrf.py`.
The three entry-point routes (signup/signin/refresh) are exempt because they
SET the cookies — requiring the cookie to first exist would be a chicken/egg.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr

from apps.api.api.dependencies.auth import (
    SESSION_COOKIE_NAME,
    get_correlation_id,
    get_current_user_id_from_cookie,
)
from apps.api.api.exceptions.domain_exceptions import (
    AuthenticationError,
    ValidationError,
)
from apps.api.api.models.auth.auth_models import SignInRequest, SignUpRequest
from apps.api.api.models.user.user_models import UserResponse
from apps.api.api.services.auth.provider import AuthProvider, get_auth_provider
from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager

REFRESH_COOKIE_NAME = "rcpt_refresh"
CSRF_COOKIE_NAME = "rcpt_csrf"

# Cookie lifetimes — match Supabase defaults, rotatable via env.
_ACCESS_TTL = int(os.getenv("AUTH_ACCESS_TTL_SECONDS", "3600"))        # 1 h
_REFRESH_TTL = int(os.getenv("AUTH_REFRESH_TTL_SECONDS", "604800"))    # 7 d
_CSRF_TTL = _REFRESH_TTL

# Path scoping — all three cookies at `/` to avoid Chrome's "set-cookie with
# non-prefix Path gets silently dropped on send" behavior. Signin runs at
# /api/v1/auth/signin; narrower paths like /api/v1/auth/session were stored
# but never re-sent by the browser. HttpOnly + SameSite=Lax on session and
# refresh is what actually guards them; narrow-pathing was marginal privacy
# that we can re-introduce once cookies provably travel.
_ACCESS_PATH = "/"
_REFRESH_PATH = "/"
_CSRF_PATH = "/"


def _cookie_secure() -> bool:
    """True in production (HTTPS), false for local dev (http://localhost)."""
    return os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")


def _cookie_samesite() -> str:
    return os.getenv("COOKIE_SAMESITE", "lax")


def _cookie_domain() -> str | None:
    """Parent-domain cookies so app.yoru.sh + api.yoru.sh share them.

    When unset (local dev), returns None → browser scopes cookie to the
    current host. In prod we set COOKIE_DOMAIN=.yoru.sh so JS on app.yoru.sh
    can read the rcpt_csrf cookie that api.yoru.sh emitted (required by the
    double-submit CSRF check)."""
    v = os.getenv("COOKIE_DOMAIN", "").strip()
    return v or None


class RefreshCookiePayload(BaseModel):
    """Only used by the service shim — the refresh router reads the cookie."""
    refresh_token: str


class SignupForm(BaseModel):
    email: EmailStr
    password: str
    first_name: str | None = None
    last_name: str | None = None
    invitation_token: str | None = None


class SessionUserResponse(BaseModel):
    user: UserResponse


def _set_session_cookies(response: Response, access: str, refresh: str) -> str:
    """Write the three auth cookies, return the CSRF token for logging."""
    csrf = secrets.token_urlsafe(32)
    secure = _cookie_secure()
    samesite = _cookie_samesite()
    domain = _cookie_domain()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=access,
        max_age=_ACCESS_TTL,
        path=_ACCESS_PATH,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh,
        max_age=_REFRESH_TTL,
        path=_REFRESH_PATH,
        httponly=True,
        secure=secure,
        samesite=samesite,
        domain=domain,
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf,
        max_age=_CSRF_TTL,
        path=_CSRF_PATH,
        httponly=False,  # readable from JS, that's the double-submit pattern
        secure=secure,
        samesite=samesite,
        domain=domain,
    )
    return csrf


def _clear_session_cookies(response: Response) -> None:
    domain = _cookie_domain()
    for name, path in (
        (SESSION_COOKIE_NAME, _ACCESS_PATH),
        (REFRESH_COOKIE_NAME, _REFRESH_PATH),
        (CSRF_COOKIE_NAME, _CSRF_PATH),
    ):
        response.delete_cookie(key=name, path=path, domain=domain)


class CookieAuthRouter:
    """Dashboard-only cookie-based auth."""

    def __init__(self) -> None:
        self.logger = LoggingController(app_name="CookieAuthRouter")
        self.router = APIRouter(prefix="/auth", tags=["auth:cookie"])
        self._service: AuthProvider | None = None
        self._setup_routes()

    def initialize_services(self) -> None:
        self._service = get_auth_provider()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.post("/signup", status_code=201, summary="Register + set session cookies")(self.signup)
        self.router.post("/signin", status_code=200, summary="Sign in + set session cookies")(self.signin)
        self.router.get("/github/start", status_code=302, summary="Begin GitHub OAuth handshake via Supabase")(self.github_start)
        self.router.get("/github/callback", status_code=302, summary="Finish GitHub OAuth; set cookies; redirect to dashboard")(self.github_callback)
        self.router.post("/session/signout", status_code=204, summary="Clear session cookies")(self.signout)
        self.router.post("/session/refresh", status_code=200, summary="Rotate session via refresh cookie")(self.refresh)
        self.router.get("/session/me", status_code=200, summary="Lightweight session-aliveness check (full profile CRUD lives at /me)")(self.me)

    async def signup(
        self,
        response: Response,
        data: SignupForm,
        correlation_id: str = Depends(get_correlation_id),
    ) -> SessionUserResponse:
        if self._service is None:
            self._service = get_auth_provider()
        signup_request = SignUpRequest(
            email=data.email,
            password=data.password,
            first_name=data.first_name,
            last_name=data.last_name,
            invitation_token=data.invitation_token,
        )
        try:
            auth_response = await self._service.sign_up(signup_request, correlation_id)
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except AuthenticationError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e
        _set_session_cookies(
            response,
            access=auth_response.access_token,
            refresh=auth_response.refresh_token,
        )
        return SessionUserResponse(user=auth_response.user)

    async def signin(
        self,
        response: Response,
        data: SignInRequest,
        correlation_id: str = Depends(get_correlation_id),
    ) -> SessionUserResponse:
        if self._service is None:
            self._service = get_auth_provider()
        try:
            auth_response = await self._service.sign_in(data, correlation_id)
        except AuthenticationError as e:
            raise HTTPException(
                status_code=401, detail="Invalid email or password"
            ) from e
        _set_session_cookies(
            response,
            access=auth_response.access_token,
            refresh=auth_response.refresh_token,
        )
        return SessionUserResponse(user=auth_response.user)

    # ── GitHub OAuth (server-driven, zero Supabase JS on the frontend) ────
    #
    #   [button on /signin or /signup] → GET /auth/github/start
    #     ↓ 302
    #   github.com/login/oauth → supabase.io → 302 /auth/github/callback?code=…
    #     ↓ backend exchanges code → mints cookies → 302 {APP_URL}/welcome
    #
    # PKCE caveat: supabase-py's default flow generates a code_verifier at
    # /start time and stores it in an in-process memory storage. Each FastAPI
    # request creates a fresh client → the verifier is gone at /callback. We
    # work around this by extracting the verifier after sign_in_with_oauth and
    # stashing it in an HttpOnly cookie, then re-seeding the fresh callback
    # client's storage before calling exchange_code_for_session.
    _OAUTH_VERIFIER_COOKIE = "rcpt_oauth_verifier"
    _OAUTH_STATE_TTL = 600  # 10 min — long enough to fumble the GitHub approve screen

    def _app_url(self) -> str:
        return os.environ.get("APP_URL", "http://localhost:5173").rstrip("/")

    def _api_base(self, request: Request) -> str:
        """Public URL of our API, used as the OAuth callback target.

        `API_URL` env wins for prod. Fallback to the request's own scheme+host
        so local dev (uvicorn on :8002) works without setting env."""
        explicit = os.environ.get("API_URL", "").rstrip("/")
        if explicit:
            return explicit
        return f"{request.url.scheme}://{request.url.netloc}"

    @staticmethod
    def _verifier_key(supabase: SupabaseManager) -> str:
        """Storage key the SDK uses for the PKCE verifier. Format matches
        supabase_auth's convention: `{storage_key}-code-verifier`."""
        return f"{supabase.client.auth._storage_key}-code-verifier"

    async def github_start(self, request: Request) -> Response:
        supabase = SupabaseManager()
        callback_url = f"{self._api_base(request)}/api/v1/auth/github/callback"
        try:
            oauth = supabase.client.auth.sign_in_with_oauth({
                "provider": "github",
                "options": {"redirect_to": callback_url},
            })
            # Pull the PKCE verifier the SDK just generated. It lives in the
            # in-process MemoryStorage — we need to persist it across the
            # round-trip to GitHub so /callback can hand it back to the SDK.
            verifier = supabase.client.auth._storage.get_item(self._verifier_key(supabase))
            if not verifier:
                raise RuntimeError("Supabase did not emit a PKCE verifier")
        except Exception as e:
            self.logger.log_error("github_start_failed", {"error": str(e)})
            return RedirectResponse(f"{self._app_url()}/signin?reason=oauth-failed", status_code=302)

        redirect = RedirectResponse(oauth.url, status_code=302)
        redirect.set_cookie(
            key=self._OAUTH_VERIFIER_COOKIE,
            value=verifier,
            max_age=self._OAUTH_STATE_TTL,
            path="/api/v1/auth/github",
            httponly=True,
            secure=_cookie_secure(),
            samesite="lax",
        )
        return redirect

    async def github_callback(
        self,
        request: Request,
        code: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ) -> Response:
        app = self._app_url()
        # Provider-level refusal (user hit "Cancel" on GitHub, etc.)
        if error or not code:
            self.logger.log_warning(
                "github_callback_refused",
                {"error": error, "detail": error_description},
            )
            return RedirectResponse(f"{app}/signin?reason=oauth-refused", status_code=302)

        verifier = request.cookies.get(self._OAUTH_VERIFIER_COOKIE)
        if not verifier:
            self.logger.log_warning("github_callback_missing_verifier", {})
            return RedirectResponse(f"{app}/signin?reason=oauth-failed", status_code=302)

        try:
            supabase = SupabaseManager()
            # Re-seed the fresh client's PKCE storage with the verifier we
            # stashed at /start time; exchange_code_for_session reads it back
            # from the same key.
            supabase.client.auth._storage.set_item(self._verifier_key(supabase), verifier)
            result = supabase.client.auth.exchange_code_for_session({"auth_code": code})
            session = getattr(result, "session", None)
            if session is None or not session.access_token or not session.refresh_token:
                raise RuntimeError("Supabase exchange returned no session tokens")
        except Exception as e:
            self.logger.log_error("github_callback_exchange_failed", {"error": str(e)})
            return RedirectResponse(f"{app}/signin?reason=oauth-failed", status_code=302)

        redirect = RedirectResponse(f"{app}/welcome", status_code=302)
        _set_session_cookies(redirect, access=session.access_token, refresh=session.refresh_token)
        # Consumed — drop the verifier cookie so a replay can't re-use it.
        redirect.delete_cookie(self._OAUTH_VERIFIER_COOKIE, path="/api/v1/auth/github")
        return redirect

    async def signout(self, request: Request, response: Response) -> Response:
        """Clear cookies unconditionally — idempotent, no 401 if already out."""
        # Best-effort server-side sign-out so Supabase drops the refresh family.
        refresh = request.cookies.get(REFRESH_COOKIE_NAME)
        access = request.cookies.get(SESSION_COOKIE_NAME)
        if refresh and access:
            try:
                supabase = SupabaseManager()
                supabase.client.auth.sign_out()
            except Exception:
                # Cookies cleared regardless — browser-side state is what matters.
                pass
        _clear_session_cookies(response)
        response.status_code = 204
        return response

    async def refresh(
        self,
        request: Request,
        response: Response,
        correlation_id: str = Depends(get_correlation_id),
    ) -> SessionUserResponse:
        if self._service is None:
            self._service = get_auth_provider()
        refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
        if not refresh_token:
            raise HTTPException(status_code=401, detail="No refresh cookie")
        from apps.api.api.models.auth.auth_models import RefreshRequest
        try:
            auth_response = await self._service.refresh_token(
                RefreshRequest(refresh_token=refresh_token), correlation_id
            )
        except AuthenticationError as e:
            # Clear cookies so the browser doesn't retry indefinitely.
            _clear_session_cookies(response)
            raise HTTPException(status_code=401, detail="Refresh expired") from e
        _set_session_cookies(
            response,
            access=auth_response.access_token,
            refresh=auth_response.refresh_token,
        )
        return SessionUserResponse(user=auth_response.user)

    async def me(
        self,
        user_id: UUID = Depends(get_current_user_id_from_cookie),
    ) -> SessionUserResponse:
        """Return minimal user info so the SPA's AuthProvider can init state.

        This endpoint is intentionally lightweight — it only proves the cookie
        is valid and returns the profile row. For full CRUD (PATCH, subscription,
        grants, groups, group features), the SPA calls the SaaSForge `/me`
        surface mounted separately.
        """
        if self._service is None:
            self._service = get_auth_provider()
        user = await self._service.get_user(user_id)
        if user is not None:
            return SessionUserResponse(user=user)
        # Cookie validated but no profile row — return a minimal shell so the
        # SPA can still initialise its auth state.
        return SessionUserResponse(
            user=UserResponse(
                id=user_id,
                email="",
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
        )
