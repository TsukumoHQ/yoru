"""Invitation service for managing user invitations."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from libs.email import EmailManager
from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from apps.api.api.models.auth.auth_models import AuthResponse, SignUpRequest
from apps.api.api.models.invitation.invitation_models import (
    InvitationAccept,
    InvitationCreate,
    InvitationListResponse,
    InvitationPublicResponse,
    InvitationResponse,
    InvitationStatus,
)
from apps.api.api.models.user.user_models import UserResponse
from apps.api.api.services.auth.auth_service import AuthService
from apps.api.api.services.auth.provider import get_auth_provider


class InvitationService:
    """Service for handling invitation operations."""

    def __init__(
        self,
        supabase: SupabaseManager | None = None,
        auth_service: AuthService | None = None,
        logger: LoggingController | None = None,
        email_manager: EmailManager | None = None,
    ):
        self.supabase = supabase or get_data_store()
        # Provider-backed (local or Supabase) — avoids eagerly building the
        # Supabase-only AuthService, which crashes a local install at import.
        self.auth_service = auth_service or get_auth_provider()
        self.logger = logger or LoggingController(app_name="InvitationService")
        self.email_manager = email_manager or EmailManager()

    async def create_invitation(
        self, data: InvitationCreate, invited_by_user_id: UUID, access_token: str, correlation_id: str
    ) -> InvitationResponse:
        """
        Create a new invitation.

        Args:
            data: Invitation creation data
            invited_by_user_id: UUID of the user creating the invitation
            correlation_id: Request correlation ID

        Returns:
            InvitationResponse with invitation data

        Raises:
            ConflictError: If email already exists or invitation pending exists
            ValidationError: If invitation creation fails
        """
        context = {
            "operation": "create_invitation",
            "component": "InvitationService",
            "correlation_id": correlation_id,
            "email": data.email,
            "invited_by": str(invited_by_user_id),
        }
        self.logger.log_info("Creating invitation", context)

        # Use authenticated Supabase client with user token for RLS
        authenticated_supabase = get_data_store(access_token=access_token)

        try:
            # Check if email already exists by trying to get user by email
            # Since we can't directly query auth.users, we'll rely on the invitation
            # acceptance flow to handle conflicts (Supabase will return error if email exists)

            # Check if pending invitation already exists
            existing_invitation = authenticated_supabase.query_records(
                "invitations",
                filters={"email": data.email, "status": "pending"},
                correlation_id=correlation_id,
            )

            if existing_invitation:
                self.logger.log_warning("Pending invitation already exists", context)
                raise ConflictError(
                    "An invitation for this email is already pending", correlation_id
                )

            # Generate secure token
            token = secrets.token_urlsafe(32)

            # Set expiration (7 days from now)
            expires_at = datetime.now(timezone.utc) + timedelta(days=7)

            # Create invitation
            invitation_data = {
                "email": data.email,
                "token": token,
                "invited_by": str(invited_by_user_id),
                "status": InvitationStatus.PENDING.value,
                "message": data.message,
                "expires_at": expires_at.isoformat(),
            }

            created_invitation = authenticated_supabase.insert_record(
                "invitations",
                invitation_data,
                correlation_id=correlation_id,
            )

            if not created_invitation:
                raise ValidationError("Failed to create invitation", correlation_id)

            # Get inviter email to enrich response
            inviter_profile = authenticated_supabase.get_record(
                "profiles",
                str(invited_by_user_id),
                correlation_id=correlation_id,
            )

            invited_by_email = inviter_profile.get("email") if inviter_profile else None

            self.logger.log_info(
                "Invitation created successfully",
                {**context, "token_prefix": token[:8]},
            )

            # Send invitation email (fail-safe: log error but don't block operation)
            try:
                # Get inviter name for email
                inviter_name = ""
                if inviter_profile:
                    first_name = inviter_profile.get("first_name", "")
                    last_name = inviter_profile.get("last_name", "")
                    inviter_name = f"{first_name} {last_name}".strip()

                if not inviter_name:
                    inviter_name = invited_by_email or "A team member"

                await self.email_manager.send_template(
                    template_name="invitation.html",
                    to_email=data.email,
                    subject=f"You've been invited to join {os.getenv('EMAIL_BRAND_NAME', 'Yoru')}",
                    context={
                        "inviter_name": inviter_name,
                        "inviter_email": invited_by_email,
                        "invite_url": f"{os.getenv('APP_URL', 'http://localhost:3000')}/accept-invite?token={token}",
                        "expires_at": expires_at.strftime("%B %d, %Y"),
                        "message": data.message,
                    },
                    correlation_id=correlation_id,
                )
                self.logger.log_info("Invitation email sent successfully", context)
            except Exception as e:
                # FAIL-SAFE: Log warning but don't fail the operation
                # Invitation is created in DB, email failure shouldn't block this
                self.logger.log_warning(
                    "Failed to send invitation email - invitation created but email not sent",
                    {**context, "email_error": str(e), "error_type": type(e).__name__},
                )

            return InvitationResponse(
                id=UUID(created_invitation["id"]),
                email=created_invitation["email"],
                status=InvitationStatus(created_invitation["status"]),
                invited_by=UUID(created_invitation["invited_by"]),
                invited_by_email=invited_by_email,
                message=created_invitation.get("message"),
                expires_at=datetime.fromisoformat(
                    created_invitation["expires_at"].replace("Z", "+00:00")
                ),
                accepted_at=(
                    datetime.fromisoformat(
                        created_invitation["accepted_at"].replace("Z", "+00:00")
                    )
                    if created_invitation.get("accepted_at")
                    else None
                ),
                created_at=datetime.fromisoformat(
                    created_invitation["created_at"].replace("Z", "+00:00")
                ),
                updated_at=datetime.fromisoformat(
                    created_invitation["updated_at"].replace("Z", "+00:00")
                ),
            )

        except (ConflictError, ValidationError):
            raise
        except Exception as e:
            self.logger.log_error(
                f"Unexpected error creating invitation: {str(e)}", context
            )
            raise ValidationError(
                "Failed to create invitation", correlation_id
            ) from e

    async def get_invitation_by_token(
        self, token: str, correlation_id: str
    ) -> InvitationPublicResponse:
        """
        Get invitation details by token (public endpoint).

        Args:
            token: Invitation token
            correlation_id: Request correlation ID

        Returns:
            InvitationPublicResponse with public invitation data

        Raises:
            NotFoundError: If invitation not found
        """
        context = {
            "operation": "get_invitation_by_token",
            "component": "InvitationService",
            "correlation_id": correlation_id,
            "token_prefix": token[:8],
        }
        self.logger.log_info("Getting invitation by token", context)

        try:
            invitation = self.supabase.query_records(
                "invitations",
                filters={"token": token},
                correlation_id=correlation_id,
            )

            if not invitation or len(invitation) == 0:
                self.logger.log_warning("Invitation not found", context)
                raise NotFoundError("Invitation not found", correlation_id)

            invitation_data = invitation[0]

            # Get inviter email
            inviter_profile = self.supabase.get_record(
                "profiles",
                invitation_data["invited_by"],
                correlation_id=correlation_id,
            )

            invited_by_email = (
                inviter_profile.get("email") if inviter_profile else "Unknown"
            )

            # Check if expired
            expires_at = datetime.fromisoformat(
                invitation_data["expires_at"].replace("Z", "+00:00")
            )
            is_expired = datetime.now(timezone.utc) > expires_at

            self.logger.log_info("Invitation retrieved successfully", context)

            return InvitationPublicResponse(
                email=invitation_data["email"],
                invited_by_email=invited_by_email,
                message=invitation_data.get("message"),
                expires_at=expires_at,
                is_expired=is_expired,
            )

        except NotFoundError:
            raise
        except Exception as e:
            self.logger.log_error(
                f"Unexpected error getting invitation: {str(e)}", context
            )
            raise NotFoundError("Invitation not found", correlation_id) from e

    async def accept_invitation(
        self, token: str, data: InvitationAccept, correlation_id: str
    ) -> AuthResponse:
        """
        Accept an invitation and create a new user account.

        Args:
            token: Invitation token
            data: Acceptance data with password and optional names
            correlation_id: Request correlation ID

        Returns:
            AuthResponse with tokens and user data

        Raises:
            NotFoundError: If invitation not found
            ValidationError: If invitation expired or already processed
            AuthenticationError: If account creation fails
        """
        context = {
            "operation": "accept_invitation",
            "component": "InvitationService",
            "correlation_id": correlation_id,
            "token_prefix": token[:8],
        }
        self.logger.log_info("Accepting invitation", context)

        try:
            # Find invitation by token
            invitation = self.supabase.query_records(
                "invitations",
                filters={"token": token},
                correlation_id=correlation_id,
            )

            if not invitation or len(invitation) == 0:
                self.logger.log_warning("Invitation not found", context)
                raise NotFoundError("Invitation not found", correlation_id)

            invitation_data = invitation[0]
            invitation_id = invitation_data["id"]

            # Check status
            if invitation_data["status"] != InvitationStatus.PENDING.value:
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
                # Auto-mark as expired
                self.supabase.update_record(
                    "invitations",
                    invitation_id,
                    {"status": InvitationStatus.EXPIRED.value},
                    correlation_id=correlation_id,
                )
                self.logger.log_warning("Invitation has expired", context)
                raise ValidationError("This invitation has expired", correlation_id)

            # Create user account via AuthService
            signup_data = SignUpRequest(
                email=invitation_data["email"],
                password=data.password,
                first_name=data.first_name,
                last_name=data.last_name,
            )

            try:
                auth_response = await self.auth_service.sign_up(
                    signup_data, correlation_id
                )
            except Exception as e:
                self.logger.log_error(
                    f"Failed to create user account: {str(e)}", context
                )
                raise AuthenticationError(
                    "Failed to create account", correlation_id
                ) from e

            # Mark invitation as accepted
            self.supabase.update_record(
                "invitations",
                invitation_id,
                {
                    "status": InvitationStatus.ACCEPTED.value,
                    "accepted_at": datetime.now(timezone.utc).isoformat(),
                },
                correlation_id=correlation_id,
            )

            self.logger.log_info("Invitation accepted successfully", context)

            return auth_response

        except (NotFoundError, ValidationError, AuthenticationError):
            raise
        except Exception as e:
            self.logger.log_error(
                f"Unexpected error accepting invitation: {str(e)}", context
            )
            raise AuthenticationError(
                "Failed to accept invitation", correlation_id
            ) from e

    async def list_sent_invitations(
        self, user_id: UUID, access_token: str, correlation_id: str
    ) -> InvitationListResponse:
        """
        List invitations sent by a user with statistics.

        Args:
            user_id: UUID of the user
            correlation_id: Request correlation ID

        Returns:
            InvitationListResponse with invitations and statistics
        """
        context = {
            "operation": "list_sent_invitations",
            "component": "InvitationService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Listing sent invitations", context)

        # Use authenticated Supabase client with user token for RLS
        authenticated_supabase = get_data_store(access_token=access_token)

        try:
            # Get all invitations sent by user
            invitations = authenticated_supabase.query_records(
                "invitations",
                filters={"invited_by": str(user_id)},
                correlation_id=correlation_id,
            )

            # Get inviter email for enrichment
            inviter_profile = authenticated_supabase.get_record(
                "profiles",
                str(user_id),
                correlation_id=correlation_id,
            )
            invited_by_email = inviter_profile.get("email") if inviter_profile else None

            # Build response list
            invitation_responses = []
            pending_count = 0
            accepted_count = 0
            expired_count = 0

            for inv in invitations:
                status = InvitationStatus(inv["status"])

                # Count by status
                if status == InvitationStatus.PENDING:
                    pending_count += 1
                elif status == InvitationStatus.ACCEPTED:
                    accepted_count += 1
                elif status == InvitationStatus.EXPIRED:
                    expired_count += 1

                invitation_responses.append(
                    InvitationResponse(
                        id=UUID(inv["id"]),
                        email=inv["email"],
                        status=status,
                        invited_by=UUID(inv["invited_by"]),
                        invited_by_email=invited_by_email,
                        message=inv.get("message"),
                        expires_at=datetime.fromisoformat(
                            inv["expires_at"].replace("Z", "+00:00")
                        ),
                        accepted_at=(
                            datetime.fromisoformat(
                                inv["accepted_at"].replace("Z", "+00:00")
                            )
                            if inv.get("accepted_at")
                            else None
                        ),
                        created_at=datetime.fromisoformat(
                            inv["created_at"].replace("Z", "+00:00")
                        ),
                        updated_at=datetime.fromisoformat(
                            inv["updated_at"].replace("Z", "+00:00")
                        ),
                    )
                )

            self.logger.log_info(
                f"Retrieved {len(invitation_responses)} invitations",
                context,
            )

            return InvitationListResponse(
                items=invitation_responses,
                total=len(invitation_responses),
                pending_count=pending_count,
                accepted_count=accepted_count,
                expired_count=expired_count,
            )

        except Exception as e:
            self.logger.log_error(
                f"Unexpected error listing invitations: {str(e)}", context
            )
            raise ValidationError(
                "Failed to list invitations", correlation_id
            ) from e

    async def cancel_invitation(
        self, invitation_id: UUID, user_id: UUID, access_token: str, correlation_id: str
    ) -> None:
        """
        Cancel a pending invitation (owner only).

        Args:
            invitation_id: UUID of the invitation to cancel
            user_id: UUID of the user requesting cancellation
            correlation_id: Request correlation ID

        Raises:
            NotFoundError: If invitation not found
            ValidationError: If not owner or not pending
        """
        context = {
            "operation": "cancel_invitation",
            "component": "InvitationService",
            "correlation_id": correlation_id,
            "invitation_id": str(invitation_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Cancelling invitation", context)

        # Use authenticated Supabase client with user token for RLS
        authenticated_supabase = get_data_store(access_token=access_token)

        try:
            # Get invitation
            invitation = authenticated_supabase.get_record(
                "invitations",
                str(invitation_id),
                correlation_id=correlation_id,
            )

            if not invitation:
                self.logger.log_warning("Invitation not found", context)
                raise NotFoundError("Invitation not found", correlation_id)

            # Check ownership
            if invitation["invited_by"] != str(user_id):
                self.logger.log_warning("User is not the owner of invitation", context)
                raise ValidationError(
                    "You can only cancel your own invitations", correlation_id
                )

            # Check status
            if invitation["status"] != InvitationStatus.PENDING.value:
                self.logger.log_warning(
                    f"Cannot cancel invitation with status: {invitation['status']}",
                    context,
                )
                raise ValidationError(
                    "Only pending invitations can be cancelled", correlation_id
                )

            # Delete invitation
            authenticated_supabase.delete_record(
                "invitations",
                str(invitation_id),
                correlation_id=correlation_id,
            )

            self.logger.log_info("Invitation cancelled successfully", context)

        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            self.logger.log_error(
                f"Unexpected error cancelling invitation: {str(e)}", context
            )
            raise ValidationError(
                "Failed to cancel invitation", correlation_id
            ) from e
