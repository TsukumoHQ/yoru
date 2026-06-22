"""First-run onboarding — the shared core behind both the web wizard
(``/api/v1/setup/*``) and the CLI (``make setup``).

Responsibilities:

* report whether the instance still needs setup,
* validate an arbitrary ``DATABASE_URL`` (so a self-hoster can point at an
  existing Postgres, or keep the bundled SQLite),
* create the first admin account and persist config to ``backend/.env``.

Both entry points call :class:`SetupService` so the web and CLI flows can never
drift apart.
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.api.routers.receipt.db import _DB_URL as CURRENT_DB_URL
from apps.api.api.routers.receipt.db import engine as app_engine
from apps.api.api.services.auth.local_models import AuthUser
from apps.api.api.services.auth.local_provider import _hash_password

_BACKEND_ROOT = Path(__file__).resolve().parents[5]
_ENV_PATH = _BACKEND_ROOT / ".env"


class SetupError(Exception):
    """Recoverable onboarding error with a human-readable message."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


def normalize_db_url(url: str) -> str:
    """Make a user-pasted URL usable by SQLAlchemy 2.0 + our installed drivers.

    * ``postgres://`` / ``postgresql://`` → ``postgresql+psycopg://`` (psycopg3,
      the driver we ship). Without this SQLAlchemy reaches for psycopg2.
    * everything else (sqlite, an already-qualified ``+driver`` URL) untouched.
    """
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _mask_url(url: str) -> str:
    """Hide credentials when surfacing a DB URL to clients/logs."""
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:****@", url)


class SetupService:
    def __init__(self) -> None:
        self.env_path = _ENV_PATH

    # ── state ────────────────────────────────────────────────────────────
    def auth_provider(self) -> str:
        return os.getenv("AUTH_PROVIDER", "local").strip().lower()

    def is_installed(self) -> bool:
        """An instance is 'installed' once it can authenticate someone.

        * supabase provider → identity is managed externally; never show the
          wizard.
        * local provider → installed once at least one admin user exists.
        """
        if self.auth_provider() != "local":
            return True
        try:
            with Session(app_engine) as session:
                admin = session.exec(
                    select(AuthUser).where(AuthUser.role == "admin")
                ).first()
                return admin is not None
        except Exception:
            # Table not created yet → definitely not installed.
            return False

    def _db_reachable(self) -> bool:
        try:
            with app_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def status(self) -> dict:
        installed = self.is_installed()
        return {
            "installed": installed,
            "needs_setup": not installed,
            "auth_provider": self.auth_provider(),
            "database_url": _mask_url(CURRENT_DB_URL),
            "database_reachable": self._db_reachable(),
            "setup_token_required": bool(os.getenv("SETUP_TOKEN", "").strip()),
        }

    # ── security gate ────────────────────────────────────────────────────
    def check_token(self, provided: str | None) -> None:
        """If ``SETUP_TOKEN`` is configured (recommended for any internet-exposed
        deployment), the wizard requires it. Otherwise setup is open until the
        first admin is created."""
        required = os.getenv("SETUP_TOKEN", "").strip()
        if required and not secrets.compare_digest(required, (provided or "").strip()):
            raise SetupError("Invalid or missing setup token")

    def ensure_not_installed(self) -> None:
        if self.is_installed():
            raise SetupError("This instance is already set up")

    # ── db validation ────────────────────────────────────────────────────
    def test_database(self, database_url: str) -> dict:
        """Open a transient engine and run ``SELECT 1``. Never touches the live
        app engine."""
        url = normalize_db_url(database_url)
        if url.startswith("sqlite:///"):
            target = url.removeprefix("sqlite:///")
            if target != ":memory:":
                Path(target).parent.mkdir(parents=True, exist_ok=True)
        try:
            test_engine = create_engine(url)
            with test_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            test_engine.dispose()
            return {"ok": True, "url": _mask_url(url)}
        except ModuleNotFoundError as e:
            return {"ok": False, "error": f"Missing database driver: {e}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── env persistence ──────────────────────────────────────────────────
    def _upsert_env(self, updates: dict[str, str]) -> None:
        """Idempotently set ``KEY=value`` lines in backend/.env."""
        lines: list[str] = []
        if self.env_path.exists():
            lines = self.env_path.read_text(encoding="utf-8").splitlines()
        remaining = dict(updates)
        out: list[str] = []
        for line in lines:
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
            if m and m.group(1) in remaining:
                key = m.group(1)
                out.append(f"{key}={remaining.pop(key)}")
            else:
                out.append(line)
        for key, value in remaining.items():
            out.append(f"{key}={value}")
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        self.env_path.write_text("\n".join(out) + "\n", encoding="utf-8")

    # ── the install action ───────────────────────────────────────────────
    def initialize(
        self,
        *,
        admin_email: str,
        admin_password: str,
        first_name: str | None = None,
        database_url: str | None = None,
        email_mode: str | None = None,
        setup_token: str | None = None,
    ) -> dict:
        """Create the first admin and persist configuration.

        Returns ``{"restart_required": bool, "admin_email": str}``. A restart is
        required only when the chosen database differs from the one the running
        process already opened (the engine is bound at import time)."""
        self.check_token(setup_token)
        self.ensure_not_installed()

        if len(admin_password) < 8:
            raise SetupError("Admin password must be at least 8 characters")
        email = admin_email.strip().lower()
        if "@" not in email:
            raise SetupError("Admin email is invalid")

        env_updates: dict[str, str] = {"AUTH_PROVIDER": "local"}

        # Always pin a JWT secret so sessions survive restarts.
        if not os.getenv("AUTH_JWT_SECRET", "").strip():
            env_updates["AUTH_JWT_SECRET"] = secrets.token_urlsafe(48)

        if email_mode:
            env_updates["EMAIL_PROVIDER"] = email_mode

        target_url = normalize_db_url(database_url) if database_url else CURRENT_DB_URL
        switching_db = target_url != CURRENT_DB_URL

        if switching_db:
            result = self.test_database(target_url)
            if not result.get("ok"):
                raise SetupError(f"Cannot connect to database: {result.get('error')}")
            env_updates["RECEIPT_DB_URL"] = target_url
            self._create_admin_in(target_url, email, admin_password, first_name)
            restart_required = True
        else:
            # Live DB — create the admin through the running engine.
            self._create_admin_in_engine(app_engine, email, admin_password, first_name)
            restart_required = False

        self._upsert_env(env_updates)
        return {"restart_required": restart_required, "admin_email": email}

    def _create_admin_in(
        self, url: str, email: str, password: str, first_name: str | None
    ) -> None:
        target_engine = create_engine(url)
        try:
            self._create_admin_in_engine(target_engine, email, password, first_name)
        finally:
            target_engine.dispose()

    def _create_admin_in_engine(
        self, target_engine, email: str, password: str, first_name: str | None
    ) -> None:
        # Populate the SQLModel table registry before create_all. The API
        # process already did this at startup, but the standalone CLI may not
        # have — import the same model modules init_db() does so a fresh target
        # DB gets the full schema.
        from apps.api.api.models import webhooks as _wh  # noqa: F401
        from apps.api.api.routers.billing import models as _bm  # noqa: F401
        from apps.api.api.routers.receipt import models as _rm  # noqa: F401
        from apps.api.api.routers.receipt.auth_sessions_model import (  # noqa: F401
            AuthSession as _AS,
        )

        SQLModel.metadata.create_all(target_engine)
        with Session(target_engine) as session:
            exists = session.exec(
                select(AuthUser).where(AuthUser.email == email)
            ).first()
            if exists is not None:
                raise SetupError("An account with this email already exists")
            now = _utcnow()
            session.add(
                AuthUser(
                    id=uuid4().hex,
                    email=email,
                    password_hash=_hash_password(password),
                    first_name=first_name,
                    role="admin",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
