"""Invitations router."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import Response

from libs.log_manager.controller import LoggingController
from apps.api.api.dependencies.auth import get_correlation_id, get_current_user_token, require_auth
from apps.api.api.models.auth.auth_models import AuthResponse
from apps.api.api.models.invitation.invitation_models import (
    InvitationAccept,
    InvitationCreate,
    InvitationListResponse,
    InvitationPublicResponse,
    InvitationResponse,
)
from apps.api.api.services.invitation.invitation_service import InvitationService


class InvitationsRouter:
    """Router for invitation endpoints."""

    def __init__(self):
        self.logger = LoggingController(app_name="InvitationsRouter")
        self.router = APIRouter(prefix="/invitations", tags=["invitations"])
        # Build eagerly — main.py mounts this router without calling
        # initialize_services(), so a lazy None left every endpoint 500-ing.
        # Safe now that InvitationService no longer constructs Supabase/SMTP eagerly.
        self.invitation_service: InvitationService = InvitationService()
        self._setup_routes()

    def initialize_services(self):
        """Initialize required services (idempotent)."""
        self.invitation_service = InvitationService()

    def get_router(self) -> APIRouter:
        """Get the FastAPI router."""
        return self.router

    def _setup_routes(self):
        """Set up invitation routes."""

        # Create invitation (authenticated)
        self.router.post(
            "",
            response_model=InvitationResponse,
            status_code=status.HTTP_201_CREATED,
            summary="Send an invitation to join",
            description="Create a new invitation for a user to join the platform. Requires authentication.",
        )(self.create_invitation)

        # List sent invitations (authenticated)
        self.router.get(
            "",
            response_model=InvitationListResponse,
            status_code=status.HTTP_200_OK,
            summary="List invitations sent by current user",
            description="Get all invitations sent by the authenticated user with statistics.",
        )(self.list_invitations)

        # Get invitation by token (public)
        self.router.get(
            "/token/{token}",
            response_model=InvitationPublicResponse,
            status_code=status.HTTP_200_OK,
            summary="Get invitation details by token",
            description="Public endpoint to view invitation details before accepting. No authentication required.",
        )(self.get_invitation)

        # Accept invitation (public - creates account)
        self.router.post(
            "/accept/{token}",
            response_model=AuthResponse,
            status_code=status.HTTP_200_OK,
            summary="Accept invitation and create account",
            description="Accept an invitation by creating a new user account. No authentication required.",
        )(self.accept_invitation)

        # Cancel invitation (authenticated - owner only)
        self.router.delete(
            "/{invitation_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            summary="Cancel a pending invitation",
            description="Cancel a pending invitation. Only the invitation creator can cancel it.",
        )(self.cancel_invitation)

    async def create_invitation(
        self,
        data: InvitationCreate,
        request: Request,
        user_id: UUID = Depends(require_auth),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ):
        """Send an invitation to join the platform."""
        context = {
            "operation": "create_invitation",
            "component": "InvitationsRouter",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Creating invitation request received", context)

        result = await self.invitation_service.create_invitation(
            data, user_id, token, correlation_id
        )

        self.logger.log_info("Invitation created successfully", context)
        return result

    async def list_invitations(
        self,
        request: Request,
        user_id: UUID = Depends(require_auth),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ):
        """List invitations sent by the current user."""
        context = {
            "operation": "list_invitations",
            "component": "InvitationsRouter",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Listing invitations request received", context)

        result = await self.invitation_service.list_sent_invitations(
            user_id, token, correlation_id
        )

        self.logger.log_info(
            f"Listed {result.total} invitations", context
        )
        return result

    async def get_invitation(
        self,
        token: str,
        request: Request,
        correlation_id: str = Depends(get_correlation_id),
    ):
        """Get invitation details by token (public endpoint)."""
        context = {
            "operation": "get_invitation",
            "component": "InvitationsRouter",
            "correlation_id": correlation_id,
            "token_prefix": token[:8],
        }
        self.logger.log_info("Get invitation by token request received", context)

        result = await self.invitation_service.get_invitation_by_token(
            token, correlation_id
        )

        self.logger.log_info("Invitation retrieved successfully", context)
        return result

    async def accept_invitation(
        self,
        token: str,
        data: InvitationAccept,
        request: Request,
        correlation_id: str = Depends(get_correlation_id),
    ):
        """Accept an invitation and create a new account."""
        context = {
            "operation": "accept_invitation",
            "component": "InvitationsRouter",
            "correlation_id": correlation_id,
            "token_prefix": token[:8],
        }
        self.logger.log_info("Accept invitation request received", context)

        result = await self.invitation_service.accept_invitation(
            token, data, correlation_id
        )

        self.logger.log_info("Invitation accepted successfully", context)
        return result

    async def cancel_invitation(
        self,
        invitation_id: UUID,
        request: Request,
        user_id: UUID = Depends(require_auth),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ):
        """Cancel a pending invitation (owner only)."""
        context = {
            "operation": "cancel_invitation",
            "component": "InvitationsRouter",
            "correlation_id": correlation_id,
            "invitation_id": str(invitation_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Cancel invitation request received", context)

        await self.invitation_service.cancel_invitation(
            invitation_id, user_id, token, correlation_id
        )

        self.logger.log_info("Invitation cancelled successfully", context)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
