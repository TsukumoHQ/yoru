"""Admin control panel for a self-hosted instance (`/api/v1/admin/instance/*`).

The on-prem operator manages the whole instance here — no cloud console:

* **Users**     — provision accounts directly (no SMTP needed), list, set role.
* **Email**     — configure SMTP so invitations/notifications actually send.

All endpoints require role=admin. User management is local-auth only (the
Supabase deployment manages users in its own console).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select

from apps.api.api.dependencies.auth import require_admin
from apps.api.api.routers.receipt.db import engine
from apps.api.api.services.auth.local_models import AuthUser
from apps.api.api.services.auth.local_provider import LocalAuthProvider, _hash_password
from apps.api.api.services.setup.setup_service import SetupService
from libs.log_manager.controller import LoggingController


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    first_name: str | None = None
    last_name: str | None = None
    role: str = Field(default="user", pattern="^(user|admin)$")


class UserRow(BaseModel):
    id: str
    email: str
    first_name: str | None = None
    last_name: str | None = None
    role: str
    created_at: datetime
    last_login_at: datetime | None = None


class RoleUpdate(BaseModel):
    role: str = Field(pattern="^(user|admin)$")


class RetentionUpdate(BaseModel):
    days: int = Field(ge=0, le=36500, description="0 = keep forever")


class EmailSettings(BaseModel):
    smtp_host: str = Field(min_length=1)
    smtp_port: int = 587
    smtp_username: str = Field(min_length=1)
    smtp_password: str = Field(min_length=1)
    smtp_from_email: EmailStr
    smtp_from_name: str | None = None


class InstanceAdminRouter:
    def __init__(self) -> None:
        self.logger = LoggingController(app_name="InstanceAdminRouter")
        self.router = APIRouter(prefix="/admin/instance", tags=["admin:instance"])
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.get("/users", summary="List instance users")(self.list_users)
        self.router.post("/users", status_code=201, summary="Create a user")(self.create_user)
        self.router.patch("/users/{user_id}/role", summary="Set a user's role")(self.set_role)
        self.router.delete("/users/{user_id}", summary="Delete a user")(self.delete_user)
        self.router.get("/settings/email", summary="Email/SMTP status (masked)")(self.get_email)
        self.router.post("/settings/email", summary="Configure SMTP")(self.set_email)
        self.router.get("/retention", summary="Data-retention policy")(self.get_retention)
        self.router.post("/retention", summary="Set retention days (0 = keep forever)")(self.set_retention)
        self.router.post("/retention/prune", summary="Delete data older than the policy now")(self.prune_now)

    # ── users ────────────────────────────────────────────────────────────
    def _require_local(self) -> None:
        if os.getenv("AUTH_PROVIDER", "local").strip().lower() != "local":
            raise HTTPException(
                status_code=400,
                detail="User management is available only with AUTH_PROVIDER=local",
            )

    async def list_users(self, _admin: UUID = Depends(require_admin)) -> list[UserRow]:
        self._require_local()
        with Session(engine) as s:
            users = s.exec(select(AuthUser)).all()
            return [
                UserRow(
                    id=str(UUID(u.id)), email=u.email, first_name=u.first_name,
                    last_name=u.last_name, role=u.role,
                    created_at=u.created_at, last_login_at=u.last_login_at,
                )
                for u in users
            ]

    async def create_user(
        self, body: CreateUserRequest, _admin: UUID = Depends(require_admin)
    ) -> UserRow:
        self._require_local()
        email = body.email.strip().lower()
        now = datetime.now(timezone.utc)
        with Session(engine) as s:
            if s.exec(select(AuthUser).where(AuthUser.email == email)).first():
                raise HTTPException(status_code=409, detail="Email already exists")
            user = AuthUser(
                id=uuid4().hex, email=email,
                password_hash=_hash_password(body.password),
                first_name=body.first_name, last_name=body.last_name,
                role=body.role, created_at=now, updated_at=now,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
        # Seed the profile document the dashboard reads.
        LocalAuthProvider()._ensure_profile(user)
        self.logger.log_info("Admin created user", {"email": email, "role": body.role})
        return UserRow(
            id=str(UUID(user.id)), email=user.email, first_name=user.first_name,
            last_name=user.last_name, role=user.role,
            created_at=user.created_at, last_login_at=None,
        )

    async def set_role(
        self, user_id: str, body: RoleUpdate, _admin: UUID = Depends(require_admin)
    ) -> UserRow:
        self._require_local()
        with Session(engine) as s:
            user = s.exec(select(AuthUser).where(AuthUser.id == UUID(user_id).hex)).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            user.role = body.role
            user.updated_at = datetime.now(timezone.utc)
            s.add(user)
            s.commit()
            s.refresh(user)
        LocalAuthProvider()._ensure_profile(user)
        return UserRow(
            id=str(UUID(user.id)), email=user.email, first_name=user.first_name,
            last_name=user.last_name, role=user.role,
            created_at=user.created_at, last_login_at=user.last_login_at,
        )

    async def delete_user(
        self, user_id: str, admin: UUID = Depends(require_admin)
    ) -> dict:
        self._require_local()
        hexid = UUID(user_id).hex
        if hexid == admin.hex:
            raise HTTPException(status_code=400, detail="You cannot delete your own account")
        with Session(engine) as s:
            user = s.exec(select(AuthUser).where(AuthUser.id == hexid)).first()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")
            s.delete(user)
            s.commit()
        return {"ok": True}

    # ── email settings ───────────────────────────────────────────────────
    async def get_email(self, _admin: UUID = Depends(require_admin)) -> dict:
        from libs.email.config import EmailConfig

        cfg = EmailConfig.from_env()
        return {
            "configured": not cfg.disabled,
            "provider": cfg.provider,
            "smtp_host": os.getenv("SMTP_HOST", ""),
            "smtp_port": int(os.getenv("SMTP_PORT", "587")),
            "smtp_username": os.getenv("SMTP_USERNAME", ""),
            "smtp_from_email": os.getenv("SMTP_FROM_EMAIL", ""),
        }

    async def set_email(
        self, body: EmailSettings, _admin: UUID = Depends(require_admin)
    ) -> dict:
        updates = {
            "EMAIL_PROVIDER": "smtp",
            "SMTP_HOST": body.smtp_host,
            "SMTP_PORT": str(body.smtp_port),
            "SMTP_USERNAME": body.smtp_username,
            "SMTP_PASSWORD": body.smtp_password,
            "SMTP_FROM_EMAIL": body.smtp_from_email,
        }
        if body.smtp_from_name:
            updates["SMTP_FROM_NAME"] = body.smtp_from_name
        SetupService()._upsert_env(updates)
        # Apply to the live process so sends work without a restart.
        os.environ.update(updates)
        self.logger.log_info("Admin configured SMTP", {"host": body.smtp_host})
        return {"ok": True, "restart_required": False}

    # ── retention ────────────────────────────────────────────────────────
    async def get_retention(self, _admin: UUID = Depends(require_admin)) -> dict:
        return {"retention_days": int(os.getenv("RETENTION_DAYS", "0"))}

    async def set_retention(
        self, body: RetentionUpdate, _admin: UUID = Depends(require_admin)
    ) -> dict:
        SetupService()._upsert_env({"RETENTION_DAYS": str(body.days)})
        os.environ["RETENTION_DAYS"] = str(body.days)
        return {"retention_days": body.days}

    async def prune_now(self, _admin: UUID = Depends(require_admin)) -> dict:
        days = int(os.getenv("RETENTION_DAYS", "0"))
        if days <= 0:
            return {"pruned_sessions": 0, "pruned_events": 0, "note": "retention disabled (keep forever)"}
        from apps.api.api.services.retention import prune_older_than

        result = prune_older_than(days)
        self.logger.log_info("Admin pruned old data", result)
        return result
