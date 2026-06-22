"""
Service métier pour le système de notifications in-app.

Respect des règles :
- python.structure.mdc : Service stateless + DI
- python.logging.mdc : LoggingController avec correlation_id
- python.errors.mdc : raise NotFoundError si notification inexistante
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.notification.notification_models import (
    NotificationListResponse,
    NotificationResponse,
    NotificationType,
)


class NotificationService:
    """Service métier pour la gestion des notifications."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="NotificationService")

    async def create_notification(
        self,
        user_id: UUID,
        title: str,
        message: str,
        type: NotificationType,
        action_url: str | None = None,
        metadata: dict | None = None,
        correlation_id: str = "",
    ) -> NotificationResponse:
        """
        Créer une notification pour un utilisateur.

        Args:
            user_id: ID de l'utilisateur destinataire
            title: Titre de la notification
            message: Message de la notification
            type: Type de notification (info, success, warning, error)
            action_url: URL d'action optionnelle
            metadata: Données additionnelles JSON
            correlation_id: Request correlation ID

        Returns:
            NotificationResponse créée

        Raises:
            ValidationError: Si les données sont invalides
        """
        context = {
            "operation": "create_notification",
            "component": "NotificationService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "type": type.value,
        }
        self.logger.log_info("Creating notification", context)

        try:
            notification_data = {
                "user_id": str(user_id),
                "title": title,
                "message": message,
                "type": type.value,
                "action_url": action_url,
                "metadata": metadata or {},
                "is_read": False,
            }

            result = self.supabase.insert_record(
                "notifications", notification_data, correlation_id=correlation_id
            )

            self.logger.log_info("Notification created successfully", context)
            return NotificationResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create notification", context)
            raise ValidationError(f"Failed to create notification: {str(e)}", correlation_id)

    async def get_user_notifications(
        self,
        user_id: UUID,
        is_read: bool | None = None,
        page: int = 1,
        page_size: int = 50,
        correlation_id: str = "",
    ) -> NotificationListResponse:
        """
        Liste paginée des notifications d'un user.

        Args:
            user_id: ID de l'utilisateur
            is_read: Filtrer par statut lu/non-lu (None = tous)
            page: Numéro de page (1-indexed)
            page_size: Nombre d'items par page
            correlation_id: Request correlation ID

        Returns:
            NotificationListResponse avec pagination
        """
        context = {
            "operation": "get_user_notifications",
            "component": "NotificationService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "is_read": is_read,
            "page": page,
            "page_size": page_size,
        }
        self.logger.log_info("Getting user notifications", context)

        try:
            # Build filters
            filters = {"user_id": str(user_id)}
            if is_read is not None:
                filters["is_read"] = is_read

            # Get total count
            total_count_query = self.supabase.client.table("notifications").select(
                "id", count="exact"
            ).match(filters)
            total_response = total_count_query.execute()
            total = total_response.count or 0

            # Get unread count
            unread_count_query = self.supabase.client.table("notifications").select(
                "id", count="exact"
            ).match({"user_id": str(user_id), "is_read": False})
            unread_response = unread_count_query.execute()
            unread_count = unread_response.count or 0

            # Get paginated notifications
            offset = (page - 1) * page_size
            notifications_query = (
                self.supabase.client.table("notifications")
                .select("*")
                .match(filters)
                .order("created_at", desc=True)
                .range(offset, offset + page_size - 1)
            )
            notifications_response = notifications_query.execute()
            notifications = notifications_response.data or []

            # Build response
            total_pages = ceil(total / page_size) if total > 0 else 0
            has_more = page < total_pages

            items = [NotificationResponse(**notif) for notif in notifications]

            self.logger.log_info(
                f"Retrieved {len(items)} notifications",
                {**context, "total": total, "unread_count": unread_count},
            )

            return NotificationListResponse(
                items=items,
                total=total,
                unread_count=unread_count,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                has_more=has_more,
            )
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get user notifications", context)
            raise

    async def get_unread_count(
        self, user_id: UUID, correlation_id: str = ""
    ) -> int:
        """
        Nombre de notifications non lues.

        Args:
            user_id: ID de l'utilisateur
            correlation_id: Request correlation ID

        Returns:
            Nombre de notifications non lues
        """
        context = {
            "operation": "get_unread_count",
            "component": "NotificationService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Getting unread count", context)

        try:
            response = (
                self.supabase.client.table("notifications")
                .select("id", count="exact")
                .match({"user_id": str(user_id), "is_read": False})
                .execute()
            )
            count = response.count or 0
            self.logger.log_info(f"Unread count: {count}", context)
            return count
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get unread count", context)
            raise

    async def mark_as_read(
        self,
        notification_id: UUID,
        user_id: UUID,
        correlation_id: str = "",
    ) -> NotificationResponse:
        """
        Marquer une notification comme lue.

        Args:
            notification_id: ID de la notification
            user_id: ID de l'utilisateur (pour vérification de propriété)
            correlation_id: Request correlation ID

        Returns:
            NotificationResponse mise à jour

        Raises:
            NotFoundError: Si notification inexistante ou pas accessible
        """
        context = {
            "operation": "mark_as_read",
            "component": "NotificationService",
            "correlation_id": correlation_id,
            "notification_id": str(notification_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Marking notification as read", context)

        try:
            # Verify notification exists and belongs to user
            notification = self.supabase.get_record(
                "notifications", str(notification_id), correlation_id=correlation_id
            )
            if not notification:
                raise NotFoundError("Notification not found", correlation_id)

            if notification["user_id"] != str(user_id):
                raise NotFoundError("Notification not found", correlation_id)

            # Update notification
            update_data = {
                "is_read": True,
                "read_at": datetime.now(timezone.utc).isoformat(),
            }
            updated = self.supabase.update_record(
                "notifications",
                str(notification_id),
                update_data,
                correlation_id=correlation_id,
            )

            self.logger.log_info("Notification marked as read", context)
            return NotificationResponse(**updated)
        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to mark notification as read", context)
            raise

    async def mark_all_as_read(
        self, user_id: UUID, correlation_id: str = ""
    ) -> int:
        """
        Marquer toutes les notifications d'un user comme lues.

        Args:
            user_id: ID de l'utilisateur
            correlation_id: Request correlation ID

        Returns:
            Nombre de notifications mises à jour
        """
        context = {
            "operation": "mark_all_as_read",
            "component": "NotificationService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Marking all notifications as read", context)

        try:
            # Update all unread notifications for user
            response = (
                self.supabase.client.table("notifications")
                .update({
                    "is_read": True,
                    "read_at": datetime.now(timezone.utc).isoformat(),
                })
                .match({"user_id": str(user_id), "is_read": False})
                .execute()
            )

            count = len(response.data) if response.data else 0
            self.logger.log_info(f"Marked {count} notifications as read", context)
            return count
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to mark all notifications as read", context)
            raise

    async def delete_notification(
        self,
        notification_id: UUID,
        user_id: UUID,
        correlation_id: str = "",
    ) -> bool:
        """
        Supprimer une notification.

        Args:
            notification_id: ID de la notification
            user_id: ID de l'utilisateur (pour vérification de propriété)
            correlation_id: Request correlation ID

        Returns:
            True si suppression réussie

        Raises:
            NotFoundError: Si notification inexistante ou pas accessible
        """
        context = {
            "operation": "delete_notification",
            "component": "NotificationService",
            "correlation_id": correlation_id,
            "notification_id": str(notification_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Deleting notification", context)

        try:
            # Verify notification exists and belongs to user
            notification = self.supabase.get_record(
                "notifications", str(notification_id), correlation_id=correlation_id
            )
            if not notification:
                raise NotFoundError("Notification not found", correlation_id)

            if notification["user_id"] != str(user_id):
                raise NotFoundError("Notification not found", correlation_id)

            # Delete notification
            self.supabase.delete_record(
                "notifications", str(notification_id), correlation_id=correlation_id
            )

            self.logger.log_info("Notification deleted successfully", context)
            return True
        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete notification", context)
            raise

    async def broadcast_notification(
        self,
        admin_id: UUID,
        title: str,
        message: str,
        type: NotificationType,
        action_url: str | None = None,
        target_user_ids: list[UUID] | None = None,
        correlation_id: str = "",
    ) -> int:
        """
        Broadcast notification système (admin only).

        Args:
            admin_id: ID de l'admin créateur
            title: Titre de la notification
            message: Message de la notification
            type: Type de notification
            action_url: URL d'action optionnelle
            target_user_ids: Liste IDs users spécifiques (None = tous les users)
            correlation_id: Request correlation ID

        Returns:
            Nombre de notifications créées

        Raises:
            ValidationError: Si la création échoue
        """
        context = {
            "operation": "broadcast_notification",
            "component": "NotificationService",
            "correlation_id": correlation_id,
            "admin_id": str(admin_id),
            "type": type.value,
            "has_targets": target_user_ids is not None,
        }
        self.logger.log_info("Broadcasting notification", context)

        try:
            # Get target user IDs
            if target_user_ids:
                user_ids = [str(uid) for uid in target_user_ids]
            else:
                # Get all user IDs from auth.users
                users_response = self.supabase.client.table("profiles").select("id").execute()
                user_ids = [user["id"] for user in users_response.data] if users_response.data else []

            if not user_ids:
                self.logger.log_warning("No users found for broadcast", context)
                return 0

            # Create notification for each user
            notifications = []
            for user_id in user_ids:
                notification_data = {
                    "user_id": user_id,
                    "title": title,
                    "message": message,
                    "type": type.value,
                    "action_url": action_url,
                    "broadcast_by": str(admin_id),
                    "is_read": False,
                }
                notifications.append(notification_data)

            # Bulk insert
            response = self.supabase.client.table("notifications").insert(notifications).execute()
            count = len(response.data) if response.data else 0

            context["recipients_count"] = count
            self.logger.log_info(f"Broadcast notification to {count} users", context)
            return count
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to broadcast notification", context)
            raise ValidationError(f"Failed to broadcast notification: {str(e)}", correlation_id)
