"""Authentication service."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID

from libs.email import EmailManager
from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from apps.api.api.exceptions.domain_exceptions import (
    AuthenticationError,
    NotFoundError,
    ValidationError,
)
from apps.api.api.models.auth.auth_models import (
    AuthResponse,
    RefreshRequest,
    SignInRequest,
    SignUpRequest,
)
from apps.api.api.models.user.user_models import UserResponse
from apps.api.api.services.auth.provider import AuthProvider
from apps.api.api.services.organization.organization_service import OrganizationService


class AuthService(AuthProvider):
    """Supabase-backed implementation of :class:`AuthProvider`.

    Selected when ``AUTH_PROVIDER=supabase``. The local, dependency-free
    default lives in :class:`~apps.api.api.services.auth.local_provider.LocalAuthProvider`.
    """

    def __init__(
        self,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
        email_manager: EmailManager | None = None,
    ):
        self.supabase = supabase or SupabaseManager()
        self.logger = logger or LoggingController(app_name="AuthService")
        self.email_manager = email_manager or EmailManager()

    async def sign_up(
        self, data: SignUpRequest, correlation_id: str
    ) -> AuthResponse:
        """
        Register a new user.

        Args:
            data: Signup request data
            correlation_id: Request correlation ID

        Returns:
            AuthResponse with tokens and user data

        Raises:
            AuthenticationError: If signup fails
        """
        context = {
            "operation": "sign_up",
            "component": "AuthService",
            "correlation_id": correlation_id,
            "email": data.email,
            "has_invitation_token": data.invitation_token is not None,
        }
        self.logger.log_info("User signup started", context)

        # If invitation token provided, validate it first
        invitation_data = None
        if data.invitation_token:
            invitation_data = await self._validate_invitation_token(
                data.invitation_token, data.email, correlation_id
            )

        try:
            auth_response = self.supabase.client.auth.sign_up(
                {
                    "email": data.email,
                    "password": data.password,
                    "options": {
                        "data": {
                            "first_name": data.first_name,
                            "last_name": data.last_name,
                        }
                    },
                }
            )

            if not auth_response.user or not auth_response.session:
                raise AuthenticationError("Signup failed", correlation_id)

            # Update profile with additional fields
            if data.first_name or data.last_name:
                self.supabase.update_record(
                    "profiles",
                    auth_response.user.id,
                    {
                        "first_name": data.first_name,
                        "last_name": data.last_name,
                    },
                    correlation_id=correlation_id,
                )

            # If invitation token was provided, mark invitation as accepted
            if invitation_data:
                self.supabase.update_record(
                    "invitations",
                    invitation_data["id"],
                    {
                        "status": "accepted",
                        "accepted_at": datetime.now(timezone.utc).isoformat(),
                    },
                    correlation_id=correlation_id,
                )
                self.logger.log_info(
                    "Invitation marked as accepted",
                    {**context, "invitation_id": invitation_data["id"]},
                )

            # Create personal organization for the new user
            # This enables the progressive multi-tenancy model
            try:
                org_service = OrganizationService(
                    access_token=auth_response.session.access_token
                )
                personal_org = await org_service.create_personal_organization(
                    owner_id=UUID(auth_response.user.id),
                    correlation_id=correlation_id,
                )
                self.logger.log_info(
                    "Personal organization created for new user",
                    {**context, "org_id": str(personal_org.id)},
                )
            except Exception as org_error:
                # Log but don't fail signup if org creation fails
                # User can create org later
                self.logger.log_warning(
                    "Failed to create personal organization",
                    {**context, "error": str(org_error)},
                )

            # Send welcome email (fail-safe: log error but don't block signup)
            try:
                user_name = data.first_name or data.email.split("@")[0]

                await self.email_manager.send_template(
                    template_name="signup_confirmation.html",
                    to_email=data.email,
                    subject=f"Welcome to {os.getenv('EMAIL_BRAND_NAME', 'Yoru')}!",
                    context={
                        "user_name": user_name,
                        "user_email": data.email,
                        "dashboard_url": f"{os.getenv('APP_URL', 'http://localhost:3000')}/dashboard",
                    },
                    correlation_id=correlation_id,
                )
                self.logger.log_info("Welcome email sent successfully", context)
            except Exception as e:
                # FAIL-SAFE: Log warning but don't fail signup
                self.logger.log_warning(
                    "Failed to send welcome email - signup successful but email not sent",
                    {**context, "email_error": str(e), "error_type": type(e).__name__},
                )

            self.logger.log_info("User signup completed", context)

            return AuthResponse(
                access_token=auth_response.session.access_token,
                refresh_token=auth_response.session.refresh_token,
                expires_in=auth_response.session.expires_in,
                user=UserResponse(
                    id=UUID(auth_response.user.id),
                    email=auth_response.user.email,
                    first_name=data.first_name,
                    last_name=data.last_name,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                ),
            )

        except AuthenticationError:
            raise
        except Exception as e:
            self.logger.log_exception(e, context)
            raise AuthenticationError("Signup failed", correlation_id) from e

    async def sign_in(
        self, data: SignInRequest, correlation_id: str
    ) -> AuthResponse:
        """
        Authenticate an existing user.

        Args:
            data: Signin request data
            correlation_id: Request correlation ID

        Returns:
            AuthResponse with tokens and user data

        Raises:
            AuthenticationError: If signin fails
        """
        context = {
            "operation": "sign_in",
            "component": "AuthService",
            "correlation_id": correlation_id,
            "email": data.email,
        }
        self.logger.log_info("User signin started", context)

        try:
            auth_response = self.supabase.client.auth.sign_in_with_password(
                {"email": data.email, "password": data.password}
            )

            if not auth_response.user or not auth_response.session:
                raise AuthenticationError("Invalid credentials", correlation_id)

            # Profile is optional — the `public.profiles` table is part of the
            # dormant Yoru org/user schemas (Phase 1). Signin must not
            # depend on it: auth success is independent of metadata presence.
            profile: dict | None = None
            try:
                profile = self.supabase.get_record(
                    "profiles",
                    auth_response.user.id,
                    correlation_id=correlation_id,
                )
            except Exception as profile_err:
                self.logger.log_warning(
                    "profiles table unavailable; continuing without metadata",
                    {**context, "error": str(profile_err)},
                )

            # last_login_at update — same fail-safe posture.
            try:
                self.supabase.update_record(
                    "profiles",
                    auth_response.user.id,
                    {"last_login_at": datetime.now().isoformat()},
                    correlation_id=correlation_id,
                )
            except Exception:
                pass

            self.logger.log_info("User signin completed", context)

            return AuthResponse(
                access_token=auth_response.session.access_token,
                refresh_token=auth_response.session.refresh_token,
                expires_in=auth_response.session.expires_in,
                user=UserResponse(
                    id=UUID(auth_response.user.id),
                    email=auth_response.user.email,
                    first_name=profile.get("first_name") if profile else None,
                    last_name=profile.get("last_name") if profile else None,
                    avatar_url=profile.get("avatar_url") if profile else None,
                    locale=profile.get("locale") if profile else "en",
                    timezone=profile.get("timezone") if profile else "UTC",
                    created_at=datetime.fromisoformat(
                        profile.get("created_at", datetime.now().isoformat())
                    )
                    if profile
                    else datetime.now(),
                    updated_at=datetime.fromisoformat(
                        profile.get("updated_at", datetime.now().isoformat())
                    )
                    if profile
                    else datetime.now(),
                ),
            )

        except AuthenticationError:
            raise
        except Exception as e:
            self.logger.log_exception(e, context)
            raise AuthenticationError("Signin failed", correlation_id) from e

    async def sign_out(self, token: str, correlation_id: str) -> None:
        """
        Sign out a user.

        Args:
            token: The user's access token
            correlation_id: Request correlation ID

        Raises:
            AuthenticationError: If signout fails
        """
        context = {
            "operation": "sign_out",
            "component": "AuthService",
            "correlation_id": correlation_id,
        }
        self.logger.log_info("User signout started", context)

        try:
            self.supabase.client.auth.sign_out()
            self.logger.log_info("User signout completed", context)

        except Exception as e:
            self.logger.log_exception(e, context)
            raise AuthenticationError("Signout failed", correlation_id) from e

    async def refresh_token(
        self, data: RefreshRequest, correlation_id: str
    ) -> AuthResponse:
        """
        Refresh an access token.

        Args:
            data: Refresh request data
            correlation_id: Request correlation ID

        Returns:
            AuthResponse with new tokens

        Raises:
            AuthenticationError: If refresh fails
        """
        context = {
            "operation": "refresh_token",
            "component": "AuthService",
            "correlation_id": correlation_id,
        }
        self.logger.log_info("Token refresh started", context)

        try:
            auth_response = self.supabase.client.auth.refresh_session(
                data.refresh_token
            )

            if not auth_response.user or not auth_response.session:
                raise AuthenticationError("Token refresh failed", correlation_id)

            # Profile is optional — same rationale as sign_in. Refresh must
            # succeed even when the `public.profiles` table doesn't exist yet
            # (pre-Phase-1 state).
            profile: dict | None = None
            try:
                profile = self.supabase.get_record(
                    "profiles",
                    auth_response.user.id,
                    correlation_id=correlation_id,
                )
            except Exception as profile_err:
                self.logger.log_warning(
                    "profiles table unavailable on refresh; continuing",
                    {**context, "error": str(profile_err)},
                )

            self.logger.log_info("Token refresh completed", context)

            return AuthResponse(
                access_token=auth_response.session.access_token,
                refresh_token=auth_response.session.refresh_token,
                expires_in=auth_response.session.expires_in,
                user=UserResponse(
                    id=UUID(auth_response.user.id),
                    email=auth_response.user.email,
                    first_name=profile.get("first_name") if profile else None,
                    last_name=profile.get("last_name") if profile else None,
                    avatar_url=profile.get("avatar_url") if profile else None,
                    locale=profile.get("locale") if profile else "en",
                    timezone=profile.get("timezone") if profile else "UTC",
                    created_at=datetime.fromisoformat(
                        profile.get("created_at", datetime.now().isoformat())
                    )
                    if profile
                    else datetime.now(),
                    updated_at=datetime.fromisoformat(
                        profile.get("updated_at", datetime.now().isoformat())
                    )
                    if profile
                    else datetime.now(),
                ),
            )

        except AuthenticationError:
            raise
        except Exception as e:
            self.logger.log_exception(e, context)
            raise AuthenticationError("Token refresh failed", correlation_id) from e

    async def verify_access_token(self, token: str) -> UUID:
        """Validate an access token against Supabase and return the user id."""
        user_response = self.supabase.client.auth.get_user(token)
        if not user_response or not user_response.user:
            raise AuthenticationError("Invalid token", "")
        return UUID(user_response.user.id)

    async def get_user(self, user_id: UUID) -> UserResponse | None:
        """Return a user's profile from the Supabase ``profiles`` table."""
        try:
            profile = self.supabase.get_record(
                "profiles", str(user_id), correlation_id=""
            )
        except Exception:
            profile = None
        if not profile:
            return None
        return UserResponse(
            id=user_id,
            email=profile.get("email", ""),
            first_name=profile.get("first_name"),
            last_name=profile.get("last_name"),
            avatar_url=profile.get("avatar_url"),
            locale=profile.get("locale", "en"),
            timezone=profile.get("timezone", "UTC"),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    async def get_user_role(self, user_id: UUID) -> str | None:
        try:
            profile = self.supabase.get_record(
                "profiles", str(user_id), correlation_id=""
            )
        except Exception:
            return None
        return profile.get("role") if profile else None

    def email_from_token(self, token: str) -> str | None:
        try:
            user_response = self.supabase.client.auth.get_user(token)
            if not user_response or not user_response.user:
                return None
            return user_response.user.email or ""
        except Exception:
            return None

    async def _validate_invitation_token(
        self, token: str, email: str, correlation_id: str
    ) -> dict:
        """
        Validate an invitation token and ensure it matches the provided email.

        Args:
            token: Invitation token
            email: Email address from signup request
            correlation_id: Request correlation ID

        Returns:
            Dictionary with invitation data

        Raises:
            ValidationError: If invitation is invalid, expired, or email doesn't match
        """
        context = {
            "operation": "validate_invitation_token",
            "component": "AuthService",
            "correlation_id": correlation_id,
            "token_prefix": token[:8],
            "email": email,
        }
        self.logger.log_info("Validating invitation token", context)

        try:
            # Find invitation by token
            invitation = self.supabase.query_records(
                "invitations",
                filters={"token": token},
                correlation_id=correlation_id,
            )

            if not invitation or len(invitation) == 0:
                self.logger.log_warning("Invitation not found", context)
                raise ValidationError("Invalid invitation token", correlation_id)

            invitation_data = invitation[0]

            # Check status
            if invitation_data["status"] != "pending":
                self.logger.log_warning(
                    f"Invitation already processed with status: {invitation_data['status']}",
                    context,
                )
                raise ValidationError(
                    "This invitation has already been processed", correlation_id
                )

            # Check expiration
            expires_at = datetime.fromisoformat(
                invitation_data["expires_at"].replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) > expires_at:
                # Mark as expired
                self.supabase.update_record(
                    "invitations",
                    invitation_data["id"],
                    {"status": "expired"},
                    correlation_id=correlation_id,
                )
                self.logger.log_warning("Invitation has expired", context)
                raise ValidationError("This invitation has expired", correlation_id)

            # Verify email matches invitation
            if invitation_data["email"].lower() != email.lower():
                self.logger.log_warning(
                    "Email does not match invitation",
                    {**context, "invitation_email": invitation_data["email"]},
                )
                raise ValidationError(
                    "Email does not match the invitation", correlation_id
                )

            self.logger.log_info("Invitation token validated successfully", context)
            return invitation_data

        except ValidationError:
            raise
        except Exception as e:
            self.logger.log_exception(e, context)
            raise ValidationError("Invalid invitation token", correlation_id) from e
