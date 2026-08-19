"""User models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    """Base user model with common fields."""

    first_name: str | None = None
    last_name: str | None = None
    avatar_url: str | None = None
    locale: str | None = "en"
    timezone: str | None = "UTC"


class UserResponse(UserBase):
    """Response model for user data."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    created_at: datetime
    updated_at: datetime
    # Multi-tenant studio super-admin flag (design 44a3774a §4). True iff the
    # instance role is 'admin' (profiles.role) — the studio operator who sees
    # across all client-orgs. Sourced + set only on GET /auth/session/me;
    # defaults False elsewhere (signup/signin), the SPA re-reads /session/me.
    # The frontend gates the cross-org switcher on this bool (never the raw role).
    is_super_admin: bool = False


class UserUpdate(BaseModel):
    """Request model for updating user profile."""

    first_name: str | None = Field(None, max_length=255)
    last_name: str | None = Field(None, max_length=255)
    avatar_url: str | None = Field(None, max_length=500)
    locale: str | None = Field(None, max_length=10)
    timezone: str | None = Field(None, max_length=50)


class UserPreferencesUpdate(BaseModel):
    """Request model for updating user preferences."""

    marketing_opt_in: bool | None = None
    locale: str | None = Field(None, max_length=10)
    timezone: str | None = Field(None, max_length=50)
