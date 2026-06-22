"""Grant service for subscription system."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.subscription.grant_models import (
    GrantCreate,
    GrantResponse,
    GrantUpdate,
)


class GrantService:
    """Service for handling user grant operations."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="GrantService")

    async def create_grant(
        self, data: GrantCreate, granted_by: UUID, correlation_id: str
    ) -> GrantResponse:
        """
        Create a new grant for a user.

        Args:
            data: Grant creation data
            granted_by: ID of the admin creating the grant
            correlation_id: Request correlation ID

        Returns:
            Created grant

        Raises:
            ValidationError: If grant already exists or validation fails
        """
        context = {
            "operation": "create_grant",
            "component": "GrantService",
            "correlation_id": correlation_id,
            "user_id": str(data.user_id),
            "feature_id": str(data.feature_id),
        }
        self.logger.log_info("Creating grant", context)

        try:
            # Verify user exists
            user = self.supabase.get_record(
                "auth.users", str(data.user_id), correlation_id=correlation_id
            )
            if not user:
                raise ValidationError("User not found", correlation_id)

            # Verify feature exists
            feature = self.supabase.get_record(
                "features", str(data.feature_id), correlation_id=correlation_id
            )
            if not feature:
                raise ValidationError("Feature not found", correlation_id)

            # Check if grant already exists
            existing_grants = self.supabase.query_records(
                "user_grants",
                filters={"user_id": str(data.user_id), "feature_id": str(data.feature_id)},
                correlation_id=correlation_id,
            )
            if existing_grants:
                raise ValidationError(
                    "Grant already exists for this user and feature", correlation_id
                )

            # Create grant
            grant_data = data.model_dump()
            grant_data["user_id"] = str(data.user_id)
            grant_data["feature_id"] = str(data.feature_id)
            grant_data["granted_by"] = str(granted_by)
            result = self.supabase.insert_record(
                "user_grants", grant_data, correlation_id=correlation_id
            )

            # Fetch feature details for response
            grant_response = {
                **result,
                "feature_key": feature["key"],
                "feature_name": feature["name"],
            }

            self.logger.log_info("Grant created successfully", context)
            return GrantResponse(**grant_response)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create grant", context)
            raise

    async def get_grant(
        self, grant_id: UUID, correlation_id: str
    ) -> GrantResponse:
        """Get grant by ID."""
        context = {
            "operation": "get_grant",
            "component": "GrantService",
            "correlation_id": correlation_id,
            "grant_id": str(grant_id),
        }
        self.logger.log_info("Getting grant", context)

        try:
            result = self.supabase.get_record(
                "user_grants", str(grant_id), correlation_id=correlation_id
            )
            if not result:
                raise NotFoundError("Grant not found", correlation_id)

            # Fetch feature details
            feature = self.supabase.get_record(
                "features", result["feature_id"], correlation_id=correlation_id
            )
            result["feature_key"] = feature["key"] if feature else ""
            result["feature_name"] = feature["name"] if feature else ""

            return GrantResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get grant", context)
            raise

    async def list_user_grants(
        self, user_id: UUID, correlation_id: str
    ) -> list[GrantResponse]:
        """
        List all active grants for a specific user using enriched view.

        This method uses the user_active_grants_enriched view which includes
        feature details via JOIN, eliminating N+1 queries (1+N → 1 query).
        """
        context = {
            "operation": "list_user_grants",
            "component": "GrantService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Listing user grants", context)

        try:
            # Single query with JOIN (replaces 1+N)
            grants = self.supabase.query_records(
                "user_active_grants_enriched",
                filters={"user_id": str(user_id)},
                correlation_id=correlation_id,
            )

            return [GrantResponse(**grant) for grant in grants]
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list user grants", context)
            raise

    async def get_active_user_grants(
        self, user_id: UUID, correlation_id: str
    ) -> list[GrantResponse]:
        """Get all active (non-expired) grants for a user."""
        context = {
            "operation": "get_active_user_grants",
            "component": "GrantService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Getting active user grants", context)

        try:
            all_grants = await self.list_user_grants(user_id, correlation_id)

            # Filter out expired grants
            now = datetime.now(timezone.utc)
            active_grants = [
                grant
                for grant in all_grants
                if grant.expires_at is None or grant.expires_at > now
            ]

            return active_grants
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get active user grants", context)
            raise

    async def check_grant_exists(
        self, user_id: UUID, feature_key: str, correlation_id: str
    ) -> GrantResponse | None:
        """Check if an active grant exists for a user and feature."""
        context = {
            "operation": "check_grant_exists",
            "component": "GrantService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "feature_key": feature_key,
        }
        self.logger.log_info("Checking grant exists", context)

        try:
            # Get feature by key
            features = self.supabase.query_records(
                "features",
                filters={"key": feature_key},
                correlation_id=correlation_id,
            )
            if not features:
                return None

            feature = features[0]

            # Get grants for user and feature
            grants = self.supabase.query_records(
                "user_grants",
                filters={"user_id": str(user_id), "feature_id": feature["id"]},
                correlation_id=correlation_id,
            )
            if not grants:
                return None

            grant = grants[0]

            # Check if grant is expired
            if grant.get("expires_at"):
                expires_at = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
                if expires_at <= datetime.now(timezone.utc):
                    return None

            grant["feature_key"] = feature["key"]
            grant["feature_name"] = feature["name"]
            return GrantResponse(**grant)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to check grant exists", context)
            raise

    async def update_grant(
        self, grant_id: UUID, data: GrantUpdate, correlation_id: str
    ) -> GrantResponse:
        """Update grant."""
        context = {
            "operation": "update_grant",
            "component": "GrantService",
            "correlation_id": correlation_id,
            "grant_id": str(grant_id),
        }
        self.logger.log_info("Updating grant", context)

        try:
            # Check if grant exists
            existing = self.supabase.get_record(
                "user_grants", str(grant_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Grant not found", correlation_id)

            # Update grant
            update_data = data.model_dump(exclude_unset=True)
            result = self.supabase.update_record(
                "user_grants", str(grant_id), update_data, correlation_id=correlation_id
            )

            # Fetch feature details
            feature = self.supabase.get_record(
                "features", result["feature_id"], correlation_id=correlation_id
            )
            result["feature_key"] = feature["key"] if feature else ""
            result["feature_name"] = feature["name"] if feature else ""

            self.logger.log_info("Grant updated successfully", context)
            return GrantResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update grant", context)
            raise

    async def delete_grant(
        self, grant_id: UUID, correlation_id: str
    ) -> None:
        """Delete grant."""
        context = {
            "operation": "delete_grant",
            "component": "GrantService",
            "correlation_id": correlation_id,
            "grant_id": str(grant_id),
        }
        self.logger.log_info("Deleting grant", context)

        try:
            # Check if grant exists
            existing = self.supabase.get_record(
                "user_grants", str(grant_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Grant not found", correlation_id)

            # Delete grant
            self.supabase.delete_record(
                "user_grants", str(grant_id), correlation_id=correlation_id
            )

            self.logger.log_info("Grant deleted successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete grant", context)
            raise

    async def list_all_grants(self, correlation_id: str) -> list[GrantResponse]:
        """
        List all active grants (admin only) using enriched view.

        This method uses the user_active_grants_enriched view which includes
        feature details via JOIN, eliminating N+1 queries.
        """
        context = {
            "operation": "list_all_grants",
            "component": "GrantService",
            "correlation_id": correlation_id,
        }
        self.logger.log_info("Listing all grants", context)

        try:
            # Single query with JOIN (replaces 1+N)
            grants = self.supabase.query_records(
                "user_active_grants_enriched",
                correlation_id=correlation_id,
            )

            return [GrantResponse(**grant) for grant in grants]
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list all grants", context)
            raise
