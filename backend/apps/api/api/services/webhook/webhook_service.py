"""
Service métier pour le système de webhooks.

Respect des règles :
- python.structure.mdc : Service stateless + DI
- python.logging.mdc : LoggingController avec correlation_id
- python.errors.mdc : raise NotFoundError si webhook inexistant
- python.async.mdc : async def, await
- scale.state.mdc : DB source de vérité, Redis pour queue
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from libs.redis.redis import RedisManager
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.webhook.webhook_models import (
    WebhookCreate,
    WebhookUpdate,
    WebhookResponse,
    WebhookListResponse,
)
from apps.api.api.services.webhook.webhook_signature import generate_webhook_secret


class WebhookService:
    """Service métier pour la gestion des webhooks."""

    QUEUE_NAME = "webhooks:queue"

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        redis: RedisManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.redis = redis or RedisManager()
        self.logger = logger or LoggingController(app_name="WebhookService")

    async def create_webhook(
        self,
        user_id: UUID,
        data: WebhookCreate,
        correlation_id: str = "",
    ) -> WebhookResponse:
        """
        Créer un webhook avec secret généré automatiquement.

        Args:
            user_id: ID de l'utilisateur créateur
            data: Données de création du webhook
            correlation_id: Request correlation ID

        Returns:
            WebhookResponse créé

        Raises:
            ValidationError: Si les données sont invalides
        """
        context = {
            "operation": "create_webhook",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "url": data.url,
            "events": data.events,
        }
        self.logger.log_info("Creating webhook", context)

        try:
            # Générer secret HMAC
            secret = generate_webhook_secret()

            webhook_data = {
                "user_id": str(user_id),
                "url": data.url,
                "secret": secret,
                "events": data.events,
                "active": data.active,
                "retry_count": 0,
            }

            result = self.supabase.insert_record(
                "webhooks", webhook_data, correlation_id=correlation_id
            )

            self.logger.log_info(
                "Webhook created successfully",
                {**context, "webhook_id": result.get("id")},
            )
            return WebhookResponse(**result)

        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create webhook", context)
            raise ValidationError(f"Failed to create webhook: {str(e)}", correlation_id)

    async def get_webhook(
        self,
        webhook_id: UUID,
        user_id: UUID,
        correlation_id: str = "",
    ) -> WebhookResponse:
        """
        Récupérer un webhook par ID (avec vérification ownership).

        Args:
            webhook_id: ID du webhook
            user_id: ID de l'utilisateur (pour vérification ownership)
            correlation_id: Request correlation ID

        Returns:
            WebhookResponse

        Raises:
            NotFoundError: Si webhook inexistant ou non accessible
        """
        context = {
            "operation": "get_webhook",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "webhook_id": str(webhook_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Getting webhook", context)

        try:
            webhook = self.supabase.get_record(
                "webhooks", str(webhook_id), correlation_id=correlation_id
            )

            if not webhook:
                raise NotFoundError("Webhook not found", correlation_id)

            if webhook["user_id"] != str(user_id):
                raise NotFoundError("Webhook not found", correlation_id)

            self.logger.log_info("Webhook retrieved successfully", context)
            return WebhookResponse(**webhook)

        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get webhook", context)
            raise

    async def list_webhooks(
        self,
        user_id: UUID,
        active_only: bool = False,
        page: int = 1,
        page_size: int = 50,
        correlation_id: str = "",
    ) -> WebhookListResponse:
        """
        Liste paginée des webhooks d'un utilisateur.

        Args:
            user_id: ID de l'utilisateur
            active_only: Filtrer uniquement les webhooks actifs
            page: Numéro de page (1-indexed)
            page_size: Nombre d'items par page
            correlation_id: Request correlation ID

        Returns:
            WebhookListResponse avec pagination
        """
        context = {
            "operation": "list_webhooks",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "active_only": active_only,
            "page": page,
            "page_size": page_size,
        }
        self.logger.log_info("Listing webhooks", context)

        try:
            # Build filters
            filters = {"user_id": str(user_id)}
            if active_only:
                filters["active"] = True

            # Get total count
            total_count_query = (
                self.supabase.client.table("webhooks")
                .select("id", count="exact")
                .match(filters)
            )
            total_response = total_count_query.execute()
            total = total_response.count or 0

            # Get paginated webhooks
            offset = (page - 1) * page_size
            webhooks_query = (
                self.supabase.client.table("webhooks")
                .select("*")
                .match(filters)
                .order("created_at", desc=True)
                .range(offset, offset + page_size - 1)
            )
            webhooks_response = webhooks_query.execute()
            webhooks = webhooks_response.data or []

            # Build response
            total_pages = ceil(total / page_size) if total > 0 else 0
            has_more = page < total_pages

            items = [WebhookResponse(**webhook) for webhook in webhooks]

            self.logger.log_info(
                f"Retrieved {len(items)} webhooks",
                {**context, "total": total},
            )

            return WebhookListResponse(
                items=items,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                has_more=has_more,
            )

        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list webhooks", context)
            raise

    async def update_webhook(
        self,
        webhook_id: UUID,
        user_id: UUID,
        data: WebhookUpdate,
        correlation_id: str = "",
    ) -> WebhookResponse:
        """
        Mettre à jour un webhook.

        Args:
            webhook_id: ID du webhook
            user_id: ID de l'utilisateur (pour vérification ownership)
            data: Données de mise à jour
            correlation_id: Request correlation ID

        Returns:
            WebhookResponse mis à jour

        Raises:
            NotFoundError: Si webhook inexistant ou non accessible
        """
        context = {
            "operation": "update_webhook",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "webhook_id": str(webhook_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Updating webhook", context)

        try:
            # Verify ownership first
            existing = await self.get_webhook(webhook_id, user_id, correlation_id)

            # Build update data (only non-None values)
            update_data = {}
            if data.url is not None:
                update_data["url"] = data.url
            if data.events is not None:
                update_data["events"] = data.events
            if data.active is not None:
                update_data["active"] = data.active

            if not update_data:
                # Nothing to update
                return existing

            updated = self.supabase.update_record(
                "webhooks",
                str(webhook_id),
                update_data,
                correlation_id=correlation_id,
            )

            self.logger.log_info("Webhook updated successfully", context)
            return WebhookResponse(**updated)

        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update webhook", context)
            raise

    async def delete_webhook(
        self,
        webhook_id: UUID,
        user_id: UUID,
        correlation_id: str = "",
    ) -> bool:
        """
        Supprimer un webhook.

        Args:
            webhook_id: ID du webhook
            user_id: ID de l'utilisateur (pour vérification ownership)
            correlation_id: Request correlation ID

        Returns:
            True si suppression réussie

        Raises:
            NotFoundError: Si webhook inexistant ou non accessible
        """
        context = {
            "operation": "delete_webhook",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "webhook_id": str(webhook_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Deleting webhook", context)

        try:
            # Verify ownership first
            await self.get_webhook(webhook_id, user_id, correlation_id)

            self.supabase.delete_record(
                "webhooks", str(webhook_id), correlation_id=correlation_id
            )

            self.logger.log_info("Webhook deleted successfully", context)
            return True

        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete webhook", context)
            raise

    async def regenerate_secret(
        self,
        webhook_id: UUID,
        user_id: UUID,
        correlation_id: str = "",
    ) -> WebhookResponse:
        """
        Régénérer le secret d'un webhook.

        Args:
            webhook_id: ID du webhook
            user_id: ID de l'utilisateur (pour vérification ownership)
            correlation_id: Request correlation ID

        Returns:
            WebhookResponse avec nouveau secret

        Raises:
            NotFoundError: Si webhook inexistant ou non accessible
        """
        context = {
            "operation": "regenerate_secret",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "webhook_id": str(webhook_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Regenerating webhook secret", context)

        try:
            # Verify ownership first
            await self.get_webhook(webhook_id, user_id, correlation_id)

            # Generate new secret
            new_secret = generate_webhook_secret()

            updated = self.supabase.update_record(
                "webhooks",
                str(webhook_id),
                {"secret": new_secret},
                correlation_id=correlation_id,
            )

            self.logger.log_info("Webhook secret regenerated successfully", context)
            return WebhookResponse(**updated)

        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to regenerate webhook secret", context)
            raise

    async def trigger_webhook(
        self,
        event_name: str,
        data: dict,
        correlation_id: str = "",
    ) -> int:
        """
        Déclencher tous les webhooks actifs pour un événement.

        Args:
            event_name: Nom de l'événement (ex: "user.created")
            data: Données de l'événement
            correlation_id: Request correlation ID (propagé depuis HTTP request)

        Returns:
            Nombre de webhooks enqueued
        """
        from uuid import uuid4

        # Générer correlation_id si absent (fallback pour appels hors contexte HTTP)
        if not correlation_id:
            correlation_id = str(uuid4())
            self.logger.log_warning(
                "No correlation_id provided, generating new one",
                {
                    "operation": "trigger_webhook",
                    "component": "WebhookService",
                    "event_name": event_name,
                    "generated_correlation_id": correlation_id,
                },
            )

        context = {
            "operation": "trigger_webhook",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "event_name": event_name,
        }
        self.logger.log_info("Triggering webhooks for event", context)

        try:
            # Import registry here to avoid circular imports
            from apps.api.api.services.webhook.webhook_registry import get_webhook_registry

            # Build payload via registry
            registry = get_webhook_registry()
            payload = registry.build_payload(event_name, data)

            # Get all active webhooks subscribed to this event
            webhooks_query = (
                self.supabase.client.table("webhooks")
                .select("id, url, secret, events")
                .eq("active", True)
                .contains("events", [event_name])
            )
            webhooks_response = webhooks_query.execute()
            webhooks = webhooks_response.data or []

            if not webhooks:
                self.logger.log_info(
                    "No active webhooks for event",
                    {**context, "webhooks_count": 0},
                )
                return 0

            # Enqueue each webhook delivery job
            for webhook in webhooks:
                job = {
                    "webhook_id": webhook["id"],
                    "url": webhook["url"],
                    "secret": webhook["secret"],
                    "event": event_name,
                    "payload": payload,
                    "correlation_id": correlation_id,
                    "attempt": 1,
                }
                await self.redis.push_to_queue(
                    self.QUEUE_NAME,
                    job,
                    correlation_id=correlation_id,
                )

            self.logger.log_info(
                "Webhooks enqueued",
                {**context, "webhooks_count": len(webhooks)},
            )
            return len(webhooks)

        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to trigger webhooks", context)
            raise

    async def update_webhook_status(
        self,
        webhook_id: str,
        success: bool,
        error_message: str | None = None,
        correlation_id: str = "",
    ) -> None:
        """
        Mettre à jour le statut d'un webhook après tentative de livraison.

        Args:
            webhook_id: ID du webhook
            success: True si livraison réussie
            error_message: Message d'erreur si échec
            correlation_id: Request correlation ID
        """
        context = {
            "operation": "update_webhook_status",
            "component": "WebhookService",
            "correlation_id": correlation_id,
            "webhook_id": webhook_id,
            "success": success,
        }
        self.logger.log_info("Updating webhook status", context)

        try:
            now = datetime.now(timezone.utc).isoformat()

            if success:
                update_data = {
                    "retry_count": 0,
                    "last_attempt_at": now,
                    "last_success_at": now,
                    "last_error": None,
                }
            else:
                # Get current retry count
                webhook = self.supabase.get_record(
                    "webhooks", webhook_id, correlation_id=correlation_id
                )
                current_retry = webhook.get("retry_count", 0) if webhook else 0

                update_data = {
                    "retry_count": current_retry + 1,
                    "last_attempt_at": now,
                    "last_error": error_message,
                }

            self.supabase.update_record(
                "webhooks",
                webhook_id,
                update_data,
                correlation_id=correlation_id,
            )

            self.logger.log_info("Webhook status updated", context)

        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update webhook status", context)
            # Don't raise - this is a non-critical operation
