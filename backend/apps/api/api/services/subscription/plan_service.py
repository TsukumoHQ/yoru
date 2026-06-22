"""Plan service for subscription system."""

from __future__ import annotations

from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.subscription.plan_models import (
    FeatureValueUpdate,
    FeatureWithValue,
    PlanCreate,
    PlanResponse,
    PlanUpdate,
)


class PlanService:
    """Service for handling plan operations."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="PlanService")

    async def create_plan(
        self, data: PlanCreate, user_id: UUID, correlation_id: str
    ) -> PlanResponse:
        """
        Create a new plan with features.

        Args:
            data: Plan creation data
            user_id: ID of the user creating the plan
            correlation_id: Request correlation ID

        Returns:
            Created plan with features

        Raises:
            ValidationError: If validation fails
        """
        context = {
            "operation": "create_plan",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_name": data.name,
        }
        self.logger.log_info("Creating plan", context)

        try:
            # Create plan
            plan_data = data.model_dump(exclude={"features"})
            plan_data["created_by"] = str(user_id)
            # Convert Decimal to float for JSON serialization
            if "price" in plan_data:
                plan_data["price"] = float(plan_data["price"])
            result = self.supabase.insert_record("plans", plan_data, correlation_id=correlation_id)
            plan_id = result["id"]

            # Add features if provided
            if data.features:
                for feature_id in data.features:
                    # Verify feature exists
                    feature = self.supabase.get_record("features", str(feature_id), correlation_id=correlation_id)
                    if not feature:
                        raise ValidationError(
                            f"Feature {feature_id} not found", correlation_id
                        )

                    # Add feature to plan with default value
                    self.supabase.insert_record(
                        "plan_features",
                        {
                            "plan_id": plan_id,
                            "feature_id": str(feature_id),
                            "value": feature["default_value"],
                        },
                        correlation_id=correlation_id,
                    )

            self.logger.log_info("Plan created successfully", context)
            return await self.get_plan_with_features(UUID(plan_id), correlation_id)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create plan", context)
            raise

    async def get_plan(self, plan_id: UUID, correlation_id: str) -> PlanResponse:
        """Get plan by ID without features."""
        context = {
            "operation": "get_plan",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_id": str(plan_id),
        }
        self.logger.log_info("Getting plan", context)

        try:
            result = self.supabase.get_record("plans", str(plan_id), correlation_id=correlation_id)
            if not result:
                raise NotFoundError("Plan not found", correlation_id)

            return PlanResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get plan", context)
            raise

    async def get_plan_with_features(
        self, plan_id: UUID, correlation_id: str
    ) -> PlanResponse:
        """Get plan with all its features."""
        context = {
            "operation": "get_plan_with_features",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_id": str(plan_id),
        }
        self.logger.log_info("Getting plan with features", context)

        try:
            # Get plan
            plan = self.supabase.get_record("plans", str(plan_id), correlation_id=correlation_id)
            if not plan:
                raise NotFoundError("Plan not found", correlation_id)

            # Get plan features
            plan_features = self.supabase.query_records(
                "plan_features", filters={"plan_id": str(plan_id)}, correlation_id=correlation_id
            )

            # Get feature details
            features_with_values = []
            for pf in plan_features:
                feature = self.supabase.get_record("features", pf["feature_id"], correlation_id=correlation_id)
                if feature:
                    features_with_values.append(
                        FeatureWithValue(
                            id=feature["id"],
                            key=feature["key"],
                            name=feature["name"],
                            type=feature["type"],
                            value=pf["value"],
                        )
                    )

            plan["features"] = features_with_values
            return PlanResponse(**plan)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get plan with features", context)
            raise

    async def list_plans(
        self, active_only: bool = True, correlation_id: str = ""
    ) -> list[PlanResponse]:
        """List all plans."""
        context = {
            "operation": "list_plans",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "active_only": active_only,
        }
        self.logger.log_info("Listing plans", context)

        try:
            filters = {"is_active": True} if active_only else {}
            results = self.supabase.query_records("plans", filters=filters, correlation_id=correlation_id)
            return [PlanResponse(**result) for result in results]
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list plans", context)
            raise

    async def update_plan(
        self, plan_id: UUID, data: PlanUpdate, correlation_id: str
    ) -> PlanResponse:
        """Update plan."""
        context = {
            "operation": "update_plan",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_id": str(plan_id),
        }
        self.logger.log_info("Updating plan", context)

        try:
            # Check if plan exists
            existing = self.supabase.get_record("plans", str(plan_id), correlation_id=correlation_id)
            if not existing:
                raise NotFoundError("Plan not found", correlation_id)

            # Update plan
            update_data = data.model_dump(exclude_unset=True)
            result = self.supabase.update_record("plans", str(plan_id), update_data, correlation_id=correlation_id)

            self.logger.log_info("Plan updated successfully", context)
            return PlanResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update plan", context)
            raise

    async def delete_plan(self, plan_id: UUID, correlation_id: str) -> None:
        """Delete plan."""
        context = {
            "operation": "delete_plan",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_id": str(plan_id),
        }
        self.logger.log_info("Deleting plan", context)

        try:
            # Check if plan exists
            existing = self.supabase.get_record("plans", str(plan_id), correlation_id=correlation_id)
            if not existing:
                raise NotFoundError("Plan not found", correlation_id)

            # Check if plan has active subscriptions
            subscriptions = self.supabase.query_records(
                "subscriptions",
                filters={"plan_id": str(plan_id), "status": "active"},
                correlation_id=correlation_id,
            )
            if subscriptions:
                raise ValidationError(
                    "Cannot delete plan with active subscriptions", correlation_id
                )

            # Delete plan (will cascade to plan_features)
            self.supabase.delete_record("plans", str(plan_id), correlation_id=correlation_id)

            self.logger.log_info("Plan deleted successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete plan", context)
            raise

    async def add_feature_to_plan(
        self, plan_id: UUID, feature_id: UUID, value: dict | bool | int | str, correlation_id: str
    ) -> None:
        """Add feature to plan with custom value."""
        context = {
            "operation": "add_feature_to_plan",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_id": str(plan_id),
            "feature_id": str(feature_id),
        }
        self.logger.log_info("Adding feature to plan", context)

        try:
            # Verify plan exists
            plan = self.supabase.get_record("plans", str(plan_id), correlation_id=correlation_id)
            if not plan:
                raise NotFoundError("Plan not found", correlation_id)

            # Verify feature exists
            feature = self.supabase.get_record("features", str(feature_id), correlation_id=correlation_id)
            if not feature:
                raise NotFoundError("Feature not found", correlation_id)

            # Check if feature already attached
            existing = self.supabase.query_records(
                "plan_features",
                filters={"plan_id": str(plan_id), "feature_id": str(feature_id)},
                correlation_id=correlation_id,
            )
            if existing:
                raise ValidationError(
                    "Feature already attached to plan", correlation_id
                )

            # Add feature to plan
            self.supabase.insert_record(
                "plan_features",
                {
                    "plan_id": str(plan_id),
                    "feature_id": str(feature_id),
                    "value": value,
                },
                correlation_id=correlation_id,
            )

            self.logger.log_info("Feature added to plan successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to add feature to plan", context)
            raise

    async def remove_feature_from_plan(
        self, plan_id: UUID, feature_id: UUID, correlation_id: str
    ) -> None:
        """Remove feature from plan."""
        context = {
            "operation": "remove_feature_from_plan",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_id": str(plan_id),
            "feature_id": str(feature_id),
        }
        self.logger.log_info("Removing feature from plan", context)

        try:
            # Check if feature is attached to plan
            existing = self.supabase.query_records(
                "plan_features",
                filters={"plan_id": str(plan_id), "feature_id": str(feature_id)},
                correlation_id=correlation_id,
            )
            if not existing:
                raise NotFoundError("Feature not attached to plan", correlation_id)

            # Delete plan_feature record
            self.supabase.client.table("plan_features").delete().eq(
                "plan_id", str(plan_id)
            ).eq("feature_id", str(feature_id)).execute()

            self.logger.log_info("Feature removed from plan successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to remove feature from plan", context)
            raise

    async def update_plan_feature_value(
        self,
        plan_id: UUID,
        feature_id: UUID,
        data: FeatureValueUpdate,
        correlation_id: str,
    ) -> None:
        """Update the value of a feature in a plan."""
        context = {
            "operation": "update_plan_feature_value",
            "component": "PlanService",
            "correlation_id": correlation_id,
            "plan_id": str(plan_id),
            "feature_id": str(feature_id),
        }
        self.logger.log_info("Updating plan feature value", context)

        try:
            # Check if feature is attached to plan
            existing = self.supabase.query_records(
                "plan_features",
                filters={"plan_id": str(plan_id), "feature_id": str(feature_id)},
                correlation_id=correlation_id,
            )
            if not existing:
                raise NotFoundError("Feature not attached to plan", correlation_id)

            # Update value
            self.supabase.client.table("plan_features").update(
                {"value": data.value}
            ).eq("plan_id", str(plan_id)).eq("feature_id", str(feature_id)).execute()

            self.logger.log_info("Plan feature value updated successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update plan feature value", context)
            raise
