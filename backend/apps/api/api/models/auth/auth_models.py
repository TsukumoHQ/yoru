"""Authentication models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class SignUpRequest(BaseModel):
    """Request model for user signup."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "user@example.com",
                    "password": "SecureP@ssw0rd",
                    "first_name": "John",
                    "last_name": "Doe",
                }
            ]
        }
    )

    email: EmailStr = Field(..., description="Valid email address", examples=["user@example.com"])
    password: str = Field(..., min_length=8, description="Minimum 8 characters", examples=["SecureP@ssw0rd"])
    first_name: str | None = Field(None, max_length=255, description="User's first name", examples=["John"])
    last_name: str | None = Field(None, max_length=255, description="User's last name", examples=["Doe"])
    invitation_token: str | None = Field(
        None, description="Optional invitation token for invited users"
    )
    setup_token: str | None = Field(
        None,
        description=(
            "Required to become the first (admin) account when SETUP_TOKEN "
            "is configured for this instance"
        ),
    )


class SignInRequest(BaseModel):
    """Request model for user signin."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "user@example.com",
                    "password": "SecureP@ssw0rd",
                }
            ]
        }
    )

    email: EmailStr = Field(..., description="Your email address", examples=["user@example.com"])
    password: str = Field(..., min_length=1, description="Your password", examples=["SecureP@ssw0rd"])


class RefreshRequest(BaseModel):
    """Request model for token refresh."""

    refresh_token: str


class AuthResponse(BaseModel):
    """Response model for authentication operations."""

    model_config = ConfigDict(from_attributes=True)

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    user: UserResponse


class TokenPayload(BaseModel):
    """JWT token payload."""

    sub: UUID
    exp: datetime
    iat: datetime


# Import at end to avoid circular import
from apps.api.api.models.user.user_models import UserResponse

# Update forward reference
AuthResponse.model_rebuild()
