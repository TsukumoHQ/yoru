"""Auth provider abstraction — decouples the dashboard auth surface from any
single identity backend.

Two backends ship in-tree:

* ``local``    — self-contained: users live in the app's own database
                 (``RECEIPT_DB_URL`` — SQLite by default, or any SQLAlchemy URL
                 incl. an existing Postgres). Passwords hashed with stdlib
                 scrypt, sessions signed with PyJWT. **Zero external service.**
* ``supabase`` — wraps a hosted (or self-hosted) Supabase GoTrue project.
                 This is the legacy default and remains fully supported.

Select via the ``AUTH_PROVIDER`` env var (default ``local``). The factory caches
a single instance per process because the request-path dependencies call it on
every authenticated request.

Every router and auth dependency talks to this interface — never to a concrete
backend — so a self-hoster can run the whole stack locally with no Supabase
account, and a managed deployment can flip one env var to use Supabase.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from uuid import UUID

from apps.api.api.models.auth.auth_models import (
    AuthResponse,
    RefreshRequest,
    SignInRequest,
    SignUpRequest,
)
from apps.api.api.models.user.user_models import UserResponse


class AuthProvider(ABC):
    """Identity backend contract used by the auth routers and dependencies."""

    @abstractmethod
    async def sign_up(self, data: SignUpRequest, correlation_id: str) -> AuthResponse:
        """Register a new user and return session tokens."""

    @abstractmethod
    async def sign_in(self, data: SignInRequest, correlation_id: str) -> AuthResponse:
        """Authenticate an existing user and return session tokens."""

    @abstractmethod
    async def refresh_token(
        self, data: RefreshRequest, correlation_id: str
    ) -> AuthResponse:
        """Rotate a refresh token into a fresh access/refresh pair."""

    @abstractmethod
    async def sign_out(self, token: str, correlation_id: str) -> None:
        """Best-effort server-side session revocation."""

    @abstractmethod
    async def verify_access_token(self, token: str) -> UUID:
        """Validate an access token and return the user id.

        This is the hot path — every authenticated request runs it. Raise on
        any invalid/expired/forged token; callers translate that to a 401.
        """

    @abstractmethod
    async def get_user(self, user_id: UUID) -> UserResponse | None:
        """Return a user's profile, or None if unknown."""

    async def get_user_role(self, user_id: UUID) -> str | None:
        """Return a user's role (e.g. ``admin``/``user``), or None if unknown.

        Default returns None (no admin surface). Providers that model roles
        override this."""
        return None

    def email_from_token(self, token: str) -> str | None:
        """Synchronously resolve an access token to the user's email.

        Bridge for the legacy *sync* data-scoping dependency in
        ``routers/receipt/deps.py``. Returns None on any invalid token.
        Providers override; the default rejects everything."""
        return None


_INSTANCE: AuthProvider | None = None


def get_auth_provider() -> AuthProvider:
    """Return the process-wide auth provider, constructed lazily from env.

    ``AUTH_PROVIDER=local`` (default) → no external dependency.
    ``AUTH_PROVIDER=supabase``       → hosted/self-hosted Supabase GoTrue.
    """
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    backend = os.getenv("AUTH_PROVIDER", "local").strip().lower()
    if backend == "supabase":
        # Imported lazily so a local-only deployment never imports the Supabase
        # SDK or requires its env vars.
        from apps.api.api.services.auth.auth_service import AuthService

        _INSTANCE = AuthService()
    elif backend == "local":
        from apps.api.api.services.auth.local_provider import LocalAuthProvider

        _INSTANCE = LocalAuthProvider()
    else:
        raise ValueError(
            f"Unknown AUTH_PROVIDER={backend!r}; expected 'local' or 'supabase'"
        )
    return _INSTANCE


def reset_auth_provider() -> None:
    """Drop the cached instance (tests / onboarding re-config)."""
    global _INSTANCE
    _INSTANCE = None
