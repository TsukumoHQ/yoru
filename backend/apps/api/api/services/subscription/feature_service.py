"""Feature service for subscription system."""

from __future__ import annotations

from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.subscription.feature_models import (
    FeatureCreate,
    FeatureResponse,
    FeatureUpdate,
)


class FeatureService:
    """Service for handling feature operations."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="FeatureService")

    async def create_feature(
        self, data: FeatureCreate, correlation_id: str
    ) -> FeatureResponse:
        """
        Create a new feature.

        Args:
            data: Feature creation data
            correlation_id: Request correlation ID

        Returns:
            Created feature

        Raises:
            ValidationError: If feature key already exists
        """
        context = {
            "operation": "create_feature",
            "component": "FeatureService",
            "correlation_id": correlation_id,
            "feature_key": data.key,
        }
        self.logger.log_info("Creating feature", context)

        try:
            # Check if feature key already exists
            existing = self.supabase.query_records(
                "features", filters={"key": data.key}, correlation_id=correlation_id
            )
            if existing:
                raise ValidationError(
                    f"Feature with key '{data.key}' already exists", correlation_id
                )

            # Create feature
            feature_data = data.model_dump()
            result = self.supabase.insert_record(
                "features", feature_data, correlation_id=correlation_id
            )

            self.logger.log_info("Feature created successfully", context)
            return FeatureResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create feature", context)
            raise

    async def get_feature(
        self, feature_id: UUID, correlation_id: str
    ) -> FeatureResponse:
        """Get feature by ID."""
        context = {
            "operation": "get_feature",
            "component": "FeatureService",
            "correlation_id": correlation_id,
            "feature_id": str(feature_id),
        }
        self.logger.log_info("Getting feature", context)

        try:
            result = self.supabase.get_record(
                "features", str(feature_id), correlation_id=correlation_id
            )
            if not result:
                raise NotFoundError("Feature not found", correlation_id)

            return FeatureResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get feature", context)
            raise

    async def get_feature_by_key(
        self, feature_key: str, correlation_id: str
    ) -> FeatureResponse | None:
        """Get feature by key."""
        context = {
            "operation": "get_feature_by_key",
            "component": "FeatureService",
            "correlation_id": correlation_id,
            "feature_key": feature_key,
        }
        self.logger.log_info("Getting feature by key", context)

        try:
            results = self.supabase.query_records(
                "features", filters={"key": feature_key}, correlation_id=correlation_id
            )
            if not results:
                return None

            return FeatureResponse(**results[0])
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get feature by key", context)
            raise

    async def list_features(self, correlation_id: str) -> list[FeatureResponse]:
        """List all features."""
        context = {
            "operation": "list_features",
            "component": "FeatureService",
            "correlation_id": correlation_id,
        }
        self.logger.log_info("Listing features", context)

        try:
            results = self.supabase.query_records(
                "features", correlation_id=correlation_id
            )
            return [FeatureResponse(**result) for result in results]
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list features", context)
            raise

    async def update_feature(
        self, feature_id: UUID, data: FeatureUpdate, correlation_id: str
    ) -> FeatureResponse:
        """Update feature."""
        context = {
            "operation": "update_feature",
            "component": "FeatureService",
            "correlation_id": correlation_id,
            "feature_id": str(feature_id),
        }
        self.logger.log_info("Updating feature", context)

        try:
            # Check if feature exists
            existing = self.supabase.get_record(
                "features", str(feature_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Feature not found", correlation_id)

            # Update feature
            update_data = data.model_dump(exclude_unset=True)
            result = self.supabase.update_record(
                "features", str(feature_id), update_data, correlation_id=correlation_id
            )

            self.logger.log_info("Feature updated successfully", context)
            return FeatureResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update feature", context)
            raise

    async def delete_feature(
        self, feature_id: UUID, correlation_id: str
    ) -> None:
        """Delete feature."""
        context = {
            "operation": "delete_feature",
            "component": "FeatureService",
            "correlation_id": correlation_id,
            "feature_id": str(feature_id),
        }
        self.logger.log_info("Deleting feature", context)

        try:
            # Check if feature exists
            existing = self.supabase.get_record(
                "features", str(feature_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Feature not found", correlation_id)

            # Delete feature (will cascade to plan_features and user_grants)
            self.supabase.delete_record(
                "features", str(feature_id), correlation_id=correlation_id
            )

            self.logger.log_info("Feature deleted successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete feature", context)
            raise
