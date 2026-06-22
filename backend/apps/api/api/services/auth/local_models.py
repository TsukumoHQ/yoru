"""Local-auth user table.

Used only when ``AUTH_PROVIDER=local``. Lives in the app's own database
(``RECEIPT_DB_URL`` — SQLite by default, or any SQLAlchemy URL), so a
self-hoster can point the whole stack at an existing Postgres without any
external identity service.

Refresh tokens are NOT stored here — they reuse the existing ``auth_sessions``
table (``routers/receipt/auth_sessions_model.AuthSession``), which already
models hashed-token rotation families.
"""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class AuthUser(SQLModel, table=True):
    """One row per local user. ``id`` is a UUID hex string (str for SQLite
    portability); the API surfaces it as a ``uuid.UUID``."""

    __tablename__ = "auth_users"

    id: str = Field(primary_key=True)
    email: str = Field(index=True, unique=True)
    # scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>
    password_hash: str
    first_name: str | None = Field(default=None)
    last_name: str | None = Field(default=None)
    avatar_url: str | None = Field(default=None)
    locale: str = Field(default="en")
    timezone: str = Field(default="UTC")
    # First user to register becomes 'admin'; everyone after is 'user'.
    role: str = Field(default="user")
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = Field(default=None)
