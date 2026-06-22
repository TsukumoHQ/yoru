"""Self-contained, DB-backed auth — the default provider.

No external identity service. Users live in the app database
(``RECEIPT_DB_URL``); passwords are hashed with stdlib scrypt; access tokens
are HS256 JWTs signed with a process-stable secret; refresh tokens are opaque
random strings stored (hashed) in ``auth_sessions`` with single-use rotation.

This is what makes "clone + run, no external dependency" true.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import jwt
from sqlmodel import Session, select

from apps.api.api.exceptions.domain_exceptions import (
    AuthenticationError,
    ValidationError,
)
from apps.api.api.models.auth.auth_models import (
    AuthResponse,
    RefreshRequest,
    SignInRequest,
    SignUpRequest,
)
from apps.api.api.models.user.user_models import UserResponse
from apps.api.api.routers.receipt.auth_sessions_model import AuthSession
from apps.api.api.routers.receipt.db import engine
from apps.api.api.services.auth.local_models import AuthUser
from apps.api.api.services.auth.provider import AuthProvider
from libs.log_manager.controller import LoggingController

_ACCESS_TTL = int(os.getenv("AUTH_ACCESS_TTL_SECONDS", "3600"))
_REFRESH_TTL = int(os.getenv("AUTH_REFRESH_TTL_SECONDS", "604800"))
_JWT_ALG = "HS256"
_JWT_ISS = "overnight-saas-local"

# scrypt cost parameters. ~16 MiB of memory per hash — strong against GPU
# cracking while staying well under interactive latency budgets.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(hash_hex) // 2,
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _jwt_secret() -> str:
    """Resolve the HS256 signing secret.

    Priority: ``AUTH_JWT_SECRET`` env → a persisted, auto-generated file under
    the data dir. The file path keeps the secret stable across restarts so
    existing sessions survive a reboot, while requiring zero configuration for
    a fresh local install. Onboarding (P2) writes ``AUTH_JWT_SECRET`` explicitly.
    """
    env = os.getenv("AUTH_JWT_SECRET", "").strip()
    if env:
        return env
    backend_root = Path(__file__).resolve().parents[5]
    secret_path = backend_root / "data" / ".auth_jwt_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()
    generated = secrets.token_urlsafe(48)
    secret_path.write_text(generated, encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return generated


def _refresh_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LocalAuthProvider(AuthProvider):
    """Database-backed auth with no external dependency."""

    def __init__(self, logger: LoggingController | None = None) -> None:
        self.logger = logger or LoggingController(app_name="LocalAuthProvider")

    # ── helpers ──────────────────────────────────────────────────────────
    def _ensure_profile(self, u: AuthUser) -> None:
        """Mirror the auth user into the `profiles` document the SaaSForge
        surface (MeRouter, dashboard) reads. Idempotent — safe on every login,
        so it also self-heals accounts created via the setup wizard or a DB
        switch."""
        try:
            from libs.datastore import get_data_store

            # Canonical dashed UUID — matches `str(user_id)` everywhere callers
            # query (the cookie verify returns UUID(sub)).
            canonical = str(UUID(u.id))
            doc = {
                "id": canonical,
                "email": u.email,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "avatar_url": u.avatar_url,
                "locale": u.locale,
                "timezone": u.timezone,
                "role": u.role,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            store = get_data_store()
            store.upsert_record("profiles", doc)
            # `user_profiles_with_email` is a Postgres view (profiles ⋈ auth email)
            # the SaaSForge user service reads; locally it's just another doc set.
            store.upsert_record("user_profiles_with_email", dict(doc))
        except Exception as e:  # never block auth on profile seeding
            self.logger.log_warning("profile seed failed", {"error": str(e)})

    def _to_user_response(self, u: AuthUser) -> UserResponse:
        return UserResponse(
            id=UUID(u.id),
            email=u.email,
            first_name=u.first_name,
            last_name=u.last_name,
            avatar_url=u.avatar_url,
            locale=u.locale,
            timezone=u.timezone,
            created_at=u.created_at,
            updated_at=u.updated_at,
        )

    def _mint_access(self, user: AuthUser) -> str:
        now = _utcnow()
        payload = {
            "sub": user.id,
            "email": user.email,
            "role": user.role,
            "iss": _JWT_ISS,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=_ACCESS_TTL)).timestamp()),
        }
        return jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALG)

    def _mint_refresh(self, session: Session, email: str) -> str:
        raw = secrets.token_urlsafe(48)
        now = _utcnow()
        row = AuthSession(
            id=uuid4().hex,
            user_email=email,
            refresh_token_hash=_refresh_hash(raw),
            issued_at=now,
            expires_at=now + timedelta(seconds=_REFRESH_TTL),
            family_id=uuid4().hex,
        )
        session.add(row)
        return raw

    def _rotate_refresh(
        self, session: Session, old_row: AuthSession
    ) -> str:
        """Single-use rotation: revoke the presented token, issue a successor
        in the same family."""
        raw = secrets.token_urlsafe(48)
        now = _utcnow()
        old_row.revoked_at = now
        old_row.last_used_at = now
        session.add(old_row)
        successor = AuthSession(
            id=uuid4().hex,
            user_email=old_row.user_email,
            refresh_token_hash=_refresh_hash(raw),
            issued_at=now,
            expires_at=now + timedelta(seconds=_REFRESH_TTL),
            family_id=old_row.family_id,
            parent_token_hash=old_row.refresh_token_hash,
        )
        session.add(successor)
        return raw

    # ── interface ────────────────────────────────────────────────────────
    async def sign_up(
        self, data: SignUpRequest, correlation_id: str
    ) -> AuthResponse:
        email = data.email.strip().lower()
        ctx = {"operation": "sign_up", "component": "LocalAuthProvider",
               "correlation_id": correlation_id, "email": email}
        self.logger.log_info("Local signup started", ctx)

        with Session(engine) as session:
            existing = session.exec(
                select(AuthUser).where(AuthUser.email == email)
            ).first()
            if existing is not None:
                raise ValidationError(
                    "An account with this email already exists", correlation_id
                )

            # First registered user is the instance admin.
            is_first = session.exec(select(AuthUser)).first() is None
            now = _utcnow()
            user = AuthUser(
                id=uuid4().hex,
                email=email,
                password_hash=_hash_password(data.password),
                first_name=data.first_name,
                last_name=data.last_name,
                role="admin" if is_first else "user",
                created_at=now,
                updated_at=now,
            )
            session.add(user)

            access = self._mint_access(user)
            refresh = self._mint_refresh(session, email)
            session.commit()
            session.refresh(user)

            self._ensure_profile(user)
            self.logger.log_info("Local signup completed", {**ctx, "role": user.role})
            return AuthResponse(
                access_token=access,
                refresh_token=refresh,
                expires_in=_ACCESS_TTL,
                user=self._to_user_response(user),
            )

    async def sign_in(
        self, data: SignInRequest, correlation_id: str
    ) -> AuthResponse:
        email = data.email.strip().lower()
        ctx = {"operation": "sign_in", "component": "LocalAuthProvider",
               "correlation_id": correlation_id, "email": email}
        self.logger.log_info("Local signin started", ctx)

        with Session(engine) as session:
            user = session.exec(
                select(AuthUser).where(AuthUser.email == email)
            ).first()
            if user is None or not _verify_password(data.password, user.password_hash):
                # Uniform error — never reveal whether the email exists.
                raise AuthenticationError("Invalid credentials", correlation_id)

            user.last_login_at = _utcnow()
            session.add(user)
            access = self._mint_access(user)
            refresh = self._mint_refresh(session, email)
            session.commit()
            session.refresh(user)

            self._ensure_profile(user)
            self.logger.log_info("Local signin completed", ctx)
            return AuthResponse(
                access_token=access,
                refresh_token=refresh,
                expires_in=_ACCESS_TTL,
                user=self._to_user_response(user),
            )

    async def refresh_token(
        self, data: RefreshRequest, correlation_id: str
    ) -> AuthResponse:
        ctx = {"operation": "refresh_token", "component": "LocalAuthProvider",
               "correlation_id": correlation_id}
        token_hash = _refresh_hash(data.refresh_token)

        with Session(engine) as session:
            row = session.exec(
                select(AuthSession).where(
                    AuthSession.refresh_token_hash == token_hash
                )
            ).first()
            if row is None or row.revoked_at is not None:
                raise AuthenticationError("Invalid refresh token", correlation_id)
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < _utcnow():
                raise AuthenticationError("Refresh token expired", correlation_id)

            user = session.exec(
                select(AuthUser).where(AuthUser.email == row.user_email)
            ).first()
            if user is None:
                raise AuthenticationError("User no longer exists", correlation_id)

            new_refresh = self._rotate_refresh(session, row)
            access = self._mint_access(user)
            session.commit()
            session.refresh(user)

            self.logger.log_info("Local token refresh completed", ctx)
            return AuthResponse(
                access_token=access,
                refresh_token=new_refresh,
                expires_in=_ACCESS_TTL,
                user=self._to_user_response(user),
            )

    async def sign_out(self, token: str, correlation_id: str) -> None:
        # Access tokens are stateless JWTs; the cookie router clears the
        # browser cookies. Nothing server-side to revoke for the access token.
        # (Refresh families could be revoked here if we tracked the access→family
        # link; out of scope for the cookie-cleared dashboard flow.)
        return None

    async def verify_access_token(self, token: str) -> UUID:
        try:
            payload = jwt.decode(
                token,
                _jwt_secret(),
                algorithms=[_JWT_ALG],
                issuer=_JWT_ISS,
                options={"require": ["exp", "sub"]},
            )
            return UUID(payload["sub"])
        except (jwt.InvalidTokenError, ValueError, KeyError) as e:
            raise AuthenticationError("Invalid or expired token", "") from e

    async def get_user(self, user_id: UUID) -> UserResponse | None:
        with Session(engine) as session:
            user = session.exec(
                select(AuthUser).where(AuthUser.id == user_id.hex)
            ).first()
            return self._to_user_response(user) if user else None

    async def get_user_role(self, user_id: UUID) -> str | None:
        with Session(engine) as session:
            user = session.exec(
                select(AuthUser).where(AuthUser.id == user_id.hex)
            ).first()
            return user.role if user else None

    def email_from_token(self, token: str) -> str | None:
        try:
            payload = jwt.decode(
                token,
                _jwt_secret(),
                algorithms=[_JWT_ALG],
                issuer=_JWT_ISS,
                options={"require": ["exp", "sub"]},
            )
            return payload.get("email")
        except (jwt.InvalidTokenError, ValueError, KeyError):
            return None
