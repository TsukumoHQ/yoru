"""Subscriptions router."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from libs.log_manager.controller import LoggingController
from apps.api.api.dependencies.auth import get_correlation_id, get_current_user_token, require_auth
from apps.api.api.exceptions.domain_exceptions import (
    NotFoundError,
    PermissionError,
    ValidationError,
)
from apps.api.api.models.subscription.subscription_models import (
    SubscriptionCreate,
    SubscriptionResponse,
)
from apps.api.api.services.subscription.subscription_service import SubscriptionService


class SubscriptionsRouter:
    """Router for subscription endpoints."""

    def __init__(self):
        self.logger = LoggingController(app_name="SubscriptionsRouter")
        self.router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
        self._setup_routes()

    def initialize_services(self):
        """Initialize required services - not used as services are created per-request."""
        pass

    def get_router(self) -> APIRouter:
        """Get the FastAPI router."""
        return self.router

    def _setup_routes(self):
        """Set up subscription routes."""
        self.router.post(
            "",
            response_model=SubscriptionResponse,
            status_code=status.HTTP_201_CREATED,
            summary="Create a new subscription",
        )(self.create_subscription)

        self.router.post(
            "/{subscription_id}/cancel",
            response_model=SubscriptionResponse,
            status_code=status.HTTP_200_OK,
            summary="Cancel a subscription",
        )(self.cancel_subscription)

    async def create_subscription(
        self,
        data: SubscriptionCreate,
        request: Request,
        user_id: UUID = Depends(require_auth),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ):
        """Create a new subscription."""
        service = SubscriptionService(access_token=token)
        return await service.create_subscription(
            data, user_id, correlation_id
        )

    async def cancel_subscription(
        self,
        subscription_id: UUID,
        request: Request,
        user_id: UUID = Depends(require_auth),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ):
        """Cancel a subscription."""
        service = SubscriptionService(access_token=token)
        try:
            return await service.cancel_subscription(
                subscription_id, user_id, correlation_id
            )
        except NotFoundError as e:
            raise HTTPException(status_code=404, detail=e.message) from e
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=e.message) from e
        except ValidationError as e:
            raise HTTPException(status_code=400, detail=e.message) from e
