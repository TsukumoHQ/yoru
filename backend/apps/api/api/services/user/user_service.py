"""User service."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import NotFoundError
from apps.api.api.models.user.user_models import UserResponse, UserUpdate


class UserService:
    """Service for handling user operations."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="UserService")

    async def get_by_id(self, user_id: UUID, correlation_id: str) -> UserResponse:
        """
        Get a user by ID using enriched view.

        This method uses the user_profiles_with_email view which includes
        email from auth.users via JOIN, reducing 2 queries to 1 query.

        Args:
            user_id: The user's UUID
            correlation_id: Request correlation ID

        Returns:
            UserResponse with user data

        Raises:
            NotFoundError: If user is not found
        """
        context = {
            "operation": "get_by_id",
            "component": "UserService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Fetching user profile", context)

        # Use view with email (single query replaces 2)
        result = self.supabase.query_records(
            "user_profiles_with_email",
            filters={"id": str(user_id)},
            correlation_id=correlation_id,
        )

        if not result:
            raise NotFoundError(f"User {user_id} not found", correlation_id)

        profile = result[0]

        return UserResponse(
            id=UUID(profile["id"]),
            email=profile.get("email", ""),
            first_name=profile.get("first_name"),
            last_name=profile.get("last_name"),
            avatar_url=profile.get("avatar_url"),
            locale=profile.get("locale", "en"),
            timezone=profile.get("timezone", "UTC"),
            created_at=datetime.fromisoformat(profile["created_at"]),
            updated_at=datetime.fromisoformat(profile["updated_at"]),
        )

    async def update(
        self, user_id: UUID, data: UserUpdate, correlation_id: str
    ) -> UserResponse:
        """
        Update a user's profile.

        Args:
            user_id: The user's UUID
            data: Update data
            correlation_id: Request correlation ID

        Returns:
            UserResponse with updated user data

        Raises:
            NotFoundError: If user is not found
        """
        context = {
            "operation": "update",
            "component": "UserService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Updating user profile", context)

        # Check if user exists
        existing = self.supabase.get_record(
            "profiles",
            str(user_id),
            correlation_id=correlation_id,
        )

        if not existing:
            raise NotFoundError(f"User {user_id} not found", correlation_id)

        # Build update dict with only provided fields
        update_data = {}
        if data.first_name is not None:
            update_data["first_name"] = data.first_name
        if data.last_name is not None:
            update_data["last_name"] = data.last_name
        if data.avatar_url is not None:
            update_data["avatar_url"] = data.avatar_url
        if data.locale is not None:
            update_data["locale"] = data.locale
        if data.timezone is not None:
            update_data["timezone"] = data.timezone

        if update_data:
            updated = self.supabase.update_record(
                "profiles",
                str(user_id),
                update_data,
                correlation_id=correlation_id,
            )
        else:
            updated = existing

        # Get user email from auth.users via Supabase Auth
        try:
            user_data = self.supabase.client.auth.admin.get_user_by_id(str(user_id))
            email = user_data.user.email if user_data and user_data.user else ""
        except Exception:
            email = ""

        self.logger.log_info("User profile updated", context)

        return UserResponse(
            id=UUID(updated["id"]),
            email=email,
            first_name=updated.get("first_name"),
            last_name=updated.get("last_name"),
            avatar_url=updated.get("avatar_url"),
            locale=updated.get("locale", "en"),
            timezone=updated.get("timezone", "UTC"),
            created_at=datetime.fromisoformat(updated["created_at"]),
            updated_at=datetime.fromisoformat(updated["updated_at"]),
        )
