"""Subscription service for subscription system."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.subscription.subscription_models import (
    SubscriptionCreate,
    SubscriptionResponse,
    SubscriptionStatus,
    SubscriptionUpdate,
)
from apps.api.api.services.subscription.promo_service import PromoService


class SubscriptionService:
    """Service for handling subscription operations with grant priority logic."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
        promo_service: PromoService | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="SubscriptionService")
        self.promo_service = promo_service or PromoService(
            access_token=access_token, supabase=self.supabase, logger=self.logger
        )

    async def create_subscription(
        self, data: SubscriptionCreate, user_id: UUID, correlation_id: str
    ) -> SubscriptionResponse:
        """
        Create a new subscription.

        Args:
            data: Subscription creation data
            user_id: ID of the user creating the subscription
            correlation_id: Request correlation ID

        Returns:
            Created subscription with features

        Raises:
            ValidationError: If validation fails
        """
        context = {
            "operation": "create_subscription",
            "component": "SubscriptionService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "plan_id": str(data.plan_id),
        }
        self.logger.log_info("Creating subscription", context)

        try:
            # Verify plan exists
            plan = self.supabase.get_record(
                "plans", str(data.plan_id), correlation_id=correlation_id
            )
            if not plan:
                raise ValidationError("Plan not found", correlation_id)

            if not plan["is_active"]:
                raise ValidationError("Plan is not active", correlation_id)

            # Check if user already has an active subscription
            existing_subscriptions = self.supabase.query_records(
                "subscriptions",
                filters={"user_id": str(user_id), "status": "active"},
                correlation_id=correlation_id,
            )
            if existing_subscriptions:
                raise ValidationError(
                    "User already has an active subscription", correlation_id
                )

            # Prepare subscription data
            subscription_data = {
                "user_id": str(user_id),
                "plan_id": str(data.plan_id),
                "status": "active",
                "start_date": datetime.now(timezone.utc).isoformat(),
            }

            # Validate and apply promo code if provided
            promo_code_id = None
            if data.promo_code:
                promo = await self.promo_service.get_promo_code_by_code(
                    data.promo_code, correlation_id
                )
                if promo:
                    promo_code_id = str(promo.id)
                    subscription_data["promo_code_id"] = promo_code_id

            # Create subscription
            result = self.supabase.insert_record(
                "subscriptions", subscription_data, correlation_id=correlation_id
            )
            subscription_id = result["id"]

            # Apply promo code if valid
            if data.promo_code and promo_code_id:
                await self.promo_service.apply_promo_code(
                    data.promo_code, user_id, UUID(subscription_id), correlation_id
                )

            self.logger.log_info("Subscription created successfully", context)
            return await self.get_subscription(UUID(subscription_id), correlation_id)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create subscription", context)
            raise

    async def get_subscription(
        self, subscription_id: UUID, correlation_id: str
    ) -> SubscriptionResponse:
        """
        Get subscription by ID with features using materialized view.

        This method queries the user_subscription_details materialized view
        for the user associated with the subscription to get pre-computed features.
        """
        context = {
            "operation": "get_subscription",
            "component": "SubscriptionService",
            "correlation_id": correlation_id,
            "subscription_id": str(subscription_id),
        }
        self.logger.log_info("Getting subscription", context)

        try:
            # First get the subscription to extract user_id
            subscription = self.supabase.get_record(
                "subscriptions", str(subscription_id), correlation_id=correlation_id
            )
            if not subscription:
                raise NotFoundError("Subscription not found", correlation_id)

            # Query materialized view using user_id to get features
            result = self.supabase.query_records(
                "user_subscription_details",
                filters={"user_id": subscription["user_id"]},
                correlation_id=correlation_id,
            )

            if result:
                # Use data from materialized view (includes features)
                return SubscriptionResponse(**result[0])
            else:
                # Fallback: subscription exists but not in materialized view (shouldn't happen for active subs)
                # Get plan details for minimal response
                plan = self.supabase.get_record(
                    "plans", subscription["plan_id"], correlation_id=correlation_id
                )
                subscription_response = {
                    **subscription,
                    "plan_name": plan["name"] if plan else "Unknown",
                    "features": {},
                }
                return SubscriptionResponse(**subscription_response)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get subscription", context)
            raise

    async def get_user_active_subscription(
        self, user_id: UUID, correlation_id: str
    ) -> SubscriptionResponse | None:
        """
        Get user's active subscription using materialized view.

        This method uses the user_subscription_details materialized view which
        pre-computes feature merging (grants override plan features), reducing
        query count from 5-13 queries to 1 query.
        """
        context = {
            "operation": "get_user_active_subscription",
            "component": "SubscriptionService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Getting user active subscription", context)

        try:
            # Single query using materialized view (replaces 5-13 queries)
            result = self.supabase.query_records(
                "user_subscription_details",
                filters={"user_id": str(user_id)},
                correlation_id=correlation_id,
            )

            if not result:
                return None

            subscription = result[0]
            return SubscriptionResponse(**subscription)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get user active subscription", context)
            raise

    async def cancel_subscription(
        self, subscription_id: UUID, correlation_id: str
    ) -> SubscriptionResponse:
        """Cancel a subscription."""
        context = {
            "operation": "cancel_subscription",
            "component": "SubscriptionService",
            "correlation_id": correlation_id,
            "subscription_id": str(subscription_id),
        }
        self.logger.log_info("Cancelling subscription", context)

        try:
            # Check if subscription exists
            subscription = self.supabase.get_record(
                "subscriptions", str(subscription_id), correlation_id=correlation_id
            )
            if not subscription:
                raise NotFoundError("Subscription not found", correlation_id)

            if subscription["status"] != "active":
                raise ValidationError("Subscription is not active", correlation_id)

            # Update subscription status
            result = self.supabase.update_record(
                "subscriptions",
                str(subscription_id),
                {"status": "cancelled", "end_date": datetime.now(timezone.utc).isoformat()},
                correlation_id=correlation_id,
            )

            self.logger.log_info("Subscription cancelled successfully", context)
            return await self.get_subscription(UUID(result["id"]), correlation_id)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to cancel subscription", context)
            raise

    async def update_subscription(
        self, subscription_id: UUID, data: SubscriptionUpdate, correlation_id: str
    ) -> SubscriptionResponse:
        """Update subscription."""
        context = {
            "operation": "update_subscription",
            "component": "SubscriptionService",
            "correlation_id": correlation_id,
            "subscription_id": str(subscription_id),
        }
        self.logger.log_info("Updating subscription", context)

        try:
            # Check if subscription exists
            existing = self.supabase.get_record(
                "subscriptions", str(subscription_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Subscription not found", correlation_id)

            # Update subscription
            update_data = data.model_dump(exclude_unset=True)
            self.supabase.update_record(
                "subscriptions", str(subscription_id), update_data, correlation_id=correlation_id
            )

            self.logger.log_info("Subscription updated successfully", context)
            return await self.get_subscription(subscription_id, correlation_id)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update subscription", context)
            raise

    async def check_feature_access(
        self, user_id: UUID, feature_key: str, correlation_id: str
    ) -> bool:
        """
        Check if user has access to a feature.
        Priority: user_grants > subscription.plan_features

        Args:
            user_id: ID of the user
            feature_key: Key of the feature to check
            correlation_id: Request correlation ID

        Returns:
            True if user has access, False otherwise
        """
        context = {
            "operation": "check_feature_access",
            "component": "SubscriptionService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "feature_key": feature_key,
        }
        self.logger.log_info("Checking feature access", context)

        try:
            # Step 1: Check user_grants first (priority)
            features = self.supabase.query_records(
                "features",
                filters={"key": feature_key},
                correlation_id=correlation_id,
            )
            if not features:
                self.logger.log_debug("Feature not found", context)
                return False

            feature = features[0]
            feature_id = feature["id"]

            # Check for active grant
            grants = self.supabase.query_records(
                "user_grants",
                filters={"user_id": str(user_id), "feature_id": feature_id},
                correlation_id=correlation_id,
            )
            if grants:
                grant = grants[0]
                # Check if grant is not expired
                if grant.get("expires_at"):
                    expires_at = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
                    if expires_at > datetime.now(timezone.utc):
                        self.logger.log_info("Feature access granted via user grant", context)
                        return True
                else:
                    # Grant without expiration
                    self.logger.log_info("Feature access granted via user grant", context)
                    return True

            # Step 2: Check subscription and plan features
            subscription = await self.get_user_active_subscription(user_id, correlation_id)
            if not subscription:
                self.logger.log_debug("No active subscription", context)
                return False

            # Check if feature is in plan
            if feature_key in subscription.features:
                feature_value = subscription.features[feature_key]
                # For flags, check if enabled
                if isinstance(feature_value, bool):
                    return feature_value
                # For quotas, check if value exists
                return feature_value is not None

            self.logger.log_debug("Feature not in plan", context)
            return False
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to check feature access", context)
            return False

    async def get_feature_value(
        self, user_id: UUID, feature_key: str, correlation_id: str
    ) -> Any:
        """
        Get feature value for a user.
        Priority: user_grants > subscription.plan_features > feature.default_value

        Args:
            user_id: ID of the user
            feature_key: Key of the feature
            correlation_id: Request correlation ID

        Returns:
            Feature value or None if not found
        """
        context = {
            "operation": "get_feature_value",
            "component": "SubscriptionService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "feature_key": feature_key,
        }
        self.logger.log_info("Getting feature value", context)

        try:
            # Get feature
            features = self.supabase.query_records(
                "features",
                filters={"key": feature_key},
                correlation_id=correlation_id,
            )
            if not features:
                return None

            feature = features[0]
            feature_id = feature["id"]

            # Step 1: Check user_grants first (priority)
            grants = self.supabase.query_records(
                "user_grants",
                filters={"user_id": str(user_id), "feature_id": feature_id},
                correlation_id=correlation_id,
            )
            if grants:
                grant = grants[0]
                # Check if grant is not expired
                if grant.get("expires_at"):
                    expires_at = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
                    if expires_at > datetime.now(timezone.utc):
                        self.logger.log_info("Feature value from user grant", context)
                        return grant["value"]
                else:
                    # Grant without expiration
                    self.logger.log_info("Feature value from user grant", context)
                    return grant["value"]

            # Step 2: Check subscription and plan features
            subscription = await self.get_user_active_subscription(user_id, correlation_id)
            if subscription and feature_key in subscription.features:
                self.logger.log_info("Feature value from subscription", context)
                return subscription.features[feature_key]

            # Step 3: Return default value
            self.logger.log_info("Feature value from default", context)
            return feature["default_value"]
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get feature value", context)
            return None

