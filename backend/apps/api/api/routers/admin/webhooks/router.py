"""
Admin router for webhook management.

Respect des règles :
- api.routes.mdc : Router class, prefix /admin/webhooks, tags
- api.authentication.mdc : Depends(require_admin) pour routes admin
- api.contracts.mdc : Status codes (201, 204), response_model
- api.errors.mdc : HTTPException dans router, mapping exceptions
- python.async.mdc : Tous endpoints async
"""

from __future__ import annotations

import time
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from libs.log_manager.controller import LoggingController
from apps.api.api.dependencies.auth import (
    get_correlation_id,
    get_current_user_token,
    require_admin,
)
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.webhook.webhook_models import (
    WebhookCreate,
    WebhookUpdate,
    WebhookResponse,
    WebhookListResponse,
    WebhookTestResponse,
    WebhookDeliveryStatus,
)
from apps.api.api.services.webhook.webhook_service import WebhookService
from apps.api.api.services.webhook.webhook_signature import generate_webhook_signature
from apps.api.api.services.webhook.webhook_registry import get_webhook_registry


class AdminWebhooksRouter:
    """Router for admin webhook management."""

    def __init__(self):
        self.logger = LoggingController(app_name="AdminWebhooksRouter")
        self.router = APIRouter(prefix="/admin/webhooks", tags=["admin", "webhooks"])
        self._setup_routes()

    def initialize_services(self) -> None:
        """Initialize required services - not used as services are created per-request."""
        pass

    def get_router(self) -> APIRouter:
        """Get the FastAPI router."""
        return self.router

    def _setup_routes(self) -> None:
        """Set up admin webhook routes."""
        # Webhook CRUD
        self.router.get("", response_model=WebhookListResponse)(self.list_webhooks)
        self.router.post(
            "",
            response_model=WebhookResponse,
            status_code=status.HTTP_201_CREATED,
        )(self.create_webhook)
        self.router.get("/{webhook_id}", response_model=WebhookResponse)(
            self.get_webhook
        )
        self.router.patch("/{webhook_id}", response_model=WebhookResponse)(
            self.update_webhook
        )
        self.router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)(
            self.delete_webhook
        )

        # Special operations
        self.router.post(
            "/{webhook_id}/regenerate-secret", response_model=WebhookResponse
        )(self.regenerate_secret)
        self.router.post("/{webhook_id}/test", response_model=WebhookTestResponse)(
            self.test_webhook
        )

        # Registry info
        self.router.get("/events/available", response_model=list[str])(
            self.list_available_events
        )

    # =============================================
    # Webhook CRUD Endpoints
    # =============================================

    async def list_webhooks(
        self,
        page: int = Query(1, ge=1, description="Page number"),
        page_size: int = Query(50, ge=1, le=200, description="Items per page"),
        active_only: bool = Query(False, description="Filter active webhooks only"),
        admin_id: UUID = Depends(require_admin),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> WebhookListResponse:
        """
        List all webhooks for the current user with pagination (admin only).

        **Parameters:**
        - **page**: Page number (default: 1)
        - **page_size**: Items per page (default: 50, max: 200)
        - **active_only**: Filter only active webhooks (default: false)

        **Returns:**
        - **items**: List of webhooks
        - **total**: Total number of webhooks
        - **page**: Current page number
        - **page_size**: Items per page
        - **total_pages**: Total number of pages
        """
        service = WebhookService(access_token=token)
        return await service.list_webhooks(
            user_id=admin_id,
            active_only=active_only,
            page=page,
            page_size=page_size,
            correlation_id=correlation_id,
        )

    async def create_webhook(
        self,
        data: WebhookCreate,
        admin_id: UUID = Depends(require_admin),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> WebhookResponse:
        """
        Create a new webhook (admin only).

        A unique secret will be automatically generated for HMAC signature verification.
        Store this secret securely - it's required to verify webhook signatures.

        **Parameters:**
        - **url**: Webhook endpoint URL (HTTPS recommended)
        - **events**: List of events to subscribe to
        - **active**: Whether the webhook is active (default: true)

        **Example:**
        ```json
        {
          "url": "https://api.example.com/webhooks/receive",
          "events": ["user.created", "payment.succeeded"],
          "active": true
        }
        ```

        **Returns:** Created webhook with generated secret
        """
        try:
            service = WebhookService(access_token=token)
            return await service.create_webhook(
                user_id=admin_id,
                data=data,
                correlation_id=correlation_id,
            )
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    async def get_webhook(
        self,
        webhook_id: UUID,
        admin_id: UUID = Depends(require_admin),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> WebhookResponse:
        """
        Get a specific webhook by ID (admin only).

        **Parameters:**
        - **webhook_id**: UUID of the webhook

        **Returns:** Webhook details including secret and delivery status
        """
        try:
            service = WebhookService(access_token=token)
            return await service.get_webhook(
                webhook_id=webhook_id,
                user_id=admin_id,
                correlation_id=correlation_id,
            )
        except NotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook not found",
            )

    async def update_webhook(
        self,
        webhook_id: UUID,
        data: WebhookUpdate,
        admin_id: UUID = Depends(require_admin),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> WebhookResponse:
        """
        Update a webhook (admin only).

        All fields are optional - only provided fields will be updated.

        **Parameters:**
        - **webhook_id**: UUID of the webhook
        - **url**: New webhook URL (optional)
        - **events**: New list of events (optional)
        - **active**: Enable/disable webhook (optional)

        **Example:**
        ```json
        {
          "events": ["user.created", "user.updated"],
          "active": true
        }
        ```

        **Returns:** Updated webhook
        """
        try:
            service = WebhookService(access_token=token)
            return await service.update_webhook(
                webhook_id=webhook_id,
                user_id=admin_id,
                data=data,
                correlation_id=correlation_id,
            )
        except NotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook not found",
            )
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    async def delete_webhook(
        self,
        webhook_id: UUID,
        admin_id: UUID = Depends(require_admin),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> Response:
        """
        Delete a webhook (admin only).

        **Parameters:**
        - **webhook_id**: UUID of the webhook to delete

        **Returns:** 204 No Content on success
        """
        try:
            service = WebhookService(access_token=token)
            await service.delete_webhook(
                webhook_id=webhook_id,
                user_id=admin_id,
                correlation_id=correlation_id,
            )
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except NotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook not found",
            )

    # =============================================
    # Special Operations
    # =============================================

    async def regenerate_secret(
        self,
        webhook_id: UUID,
        admin_id: UUID = Depends(require_admin),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> WebhookResponse:
        """
        Regenerate the secret for a webhook (admin only).

        This will invalidate the previous secret immediately.
        Make sure to update the secret in your receiving application.

        **Parameters:**
        - **webhook_id**: UUID of the webhook

        **Returns:** Updated webhook with new secret
        """
        try:
            service = WebhookService(access_token=token)
            return await service.regenerate_secret(
                webhook_id=webhook_id,
                user_id=admin_id,
                correlation_id=correlation_id,
            )
        except NotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook not found",
            )

    async def test_webhook(
        self,
        webhook_id: UUID,
        admin_id: UUID = Depends(require_admin),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> WebhookTestResponse:
        """
        Send a test event to a webhook (admin only).

        This will send a test payload to verify the webhook is configured correctly.
        The test event will include a proper HMAC signature.

        **Parameters:**
        - **webhook_id**: UUID of the webhook to test

        **Returns:** Test result with status and response details
        """
        context = {
            "operation": "test_webhook",
            "component": "AdminWebhooksRouter",
            "correlation_id": correlation_id,
            "webhook_id": str(webhook_id),
        }

        try:
            service = WebhookService(access_token=token)
            webhook = await service.get_webhook(
                webhook_id=webhook_id,
                user_id=admin_id,
                correlation_id=correlation_id,
            )

            # Build test payload
            registry = get_webhook_registry()
            test_payload = registry.build_payload(
                "webhook.test",
                {"webhook_id": str(webhook_id), "test": True},
            )

            # Generate signature (vuln-0010: webhook.secret is redacted on
            # get_webhook's response — the decrypted value comes from the
            # internal-only get_webhook_secret instead).
            secret = await service.get_webhook_secret(
                webhook_id=webhook_id,
                user_id=admin_id,
                correlation_id=correlation_id,
            )
            signature = generate_webhook_signature(test_payload, secret)

            # Send test request
            start_time = time.time()
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        webhook.url,
                        json=test_payload,
                        headers={
                            "X-Webhook-Signature": signature,
                            "X-Webhook-Event": "webhook.test",
                            "X-Correlation-ID": correlation_id,
                        },
                    )
                    response.raise_for_status()

                response_time_ms = (time.time() - start_time) * 1000

                self.logger.log_info(
                    "Webhook test successful",
                    {**context, "response_code": response.status_code},
                )

                return WebhookTestResponse(
                    webhook_id=webhook_id,
                    status=WebhookDeliveryStatus.SUCCESS,
                    response_code=response.status_code,
                    response_time_ms=round(response_time_ms, 2),
                )

            except httpx.TimeoutException:
                response_time_ms = (time.time() - start_time) * 1000
                self.logger.log_warning("Webhook test timeout", context)
                return WebhookTestResponse(
                    webhook_id=webhook_id,
                    status=WebhookDeliveryStatus.FAILED,
                    response_time_ms=round(response_time_ms, 2),
                    error="Request timeout (30s)",
                )

            except httpx.HTTPStatusError as e:
                response_time_ms = (time.time() - start_time) * 1000
                self.logger.log_warning(
                    "Webhook test failed with HTTP error",
                    {**context, "status_code": e.response.status_code},
                )
                return WebhookTestResponse(
                    webhook_id=webhook_id,
                    status=WebhookDeliveryStatus.FAILED,
                    response_code=e.response.status_code,
                    response_time_ms=round(response_time_ms, 2),
                    error=f"HTTP {e.response.status_code}",
                )

            except httpx.RequestError as e:
                response_time_ms = (time.time() - start_time) * 1000
                self.logger.log_warning(
                    "Webhook test failed with request error",
                    {**context, "error": str(e)},
                )
                return WebhookTestResponse(
                    webhook_id=webhook_id,
                    status=WebhookDeliveryStatus.FAILED,
                    response_time_ms=round(response_time_ms, 2),
                    error=str(e),
                )

        except NotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Webhook not found",
            )

    async def list_available_events(
        self,
        admin_id: UUID = Depends(require_admin),
        correlation_id: str = Depends(get_correlation_id),
    ) -> list[str]:
        """
        List all available webhook events (admin only).

        Returns the list of event types that can be subscribed to.
        Custom events registered via the WebhookEventRegistry are included.

        **Returns:** List of available event names
        """
        registry = get_webhook_registry()
        return registry.list_events()
