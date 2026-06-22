"""Promo code service for subscription system."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.subscription.promo_models import (
    DiscountType,
    PromoCodeCreate,
    PromoCodeResponse,
    PromoCodeUpdate,
    PromoValidateRequest,
    PromoValidateResponse,
)


class PromoService:
    """Service for handling promo code operations."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="PromoService")

    async def create_promo_code(
        self, data: PromoCodeCreate, user_id: UUID, correlation_id: str
    ) -> PromoCodeResponse:
        """
        Create a new promo code.

        Args:
            data: Promo code creation data
            user_id: ID of the admin creating the promo code
            correlation_id: Request correlation ID

        Returns:
            Created promo code

        Raises:
            ValidationError: If promo code already exists
        """
        context = {
            "operation": "create_promo_code",
            "component": "PromoService",
            "correlation_id": correlation_id,
            "promo_code": data.code,
        }
        self.logger.log_info("Creating promo code", context)

        try:
            # Check if promo code already exists
            existing = self.supabase.query_records(
                "promo_codes",
                filters={"code": data.code},
                correlation_id=correlation_id,
            )
            if existing:
                raise ValidationError(
                    f"Promo code '{data.code}' already exists", correlation_id
                )

            # Create promo code
            promo_data = data.model_dump(mode='json')  # Use JSON mode for proper serialization
            promo_data["created_by"] = str(user_id)
            # Convert Decimal to float for JSON serialization
            if "value" in promo_data:
                promo_data["value"] = float(promo_data["value"])
            result = self.supabase.insert_record(
                "promo_codes", promo_data, correlation_id=correlation_id
            )

            self.logger.log_info("Promo code created successfully", context)
            return PromoCodeResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create promo code", context)
            raise

    async def get_promo_code(
        self, promo_id: UUID, correlation_id: str
    ) -> PromoCodeResponse:
        """Get promo code by ID."""
        context = {
            "operation": "get_promo_code",
            "component": "PromoService",
            "correlation_id": correlation_id,
            "promo_id": str(promo_id),
        }
        self.logger.log_info("Getting promo code", context)

        try:
            result = self.supabase.get_record(
                "promo_codes", str(promo_id), correlation_id=correlation_id
            )
            if not result:
                raise NotFoundError("Promo code not found", correlation_id)

            return PromoCodeResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get promo code", context)
            raise

    async def get_promo_code_by_code(
        self, code: str, correlation_id: str
    ) -> PromoCodeResponse | None:
        """Get promo code by code string."""
        context = {
            "operation": "get_promo_code_by_code",
            "component": "PromoService",
            "correlation_id": correlation_id,
            "code": code,
        }
        self.logger.log_info("Getting promo code by code", context)

        try:
            results = self.supabase.query_records(
                "promo_codes",
                filters={"code": code},
                correlation_id=correlation_id,
            )
            if not results:
                return None

            return PromoCodeResponse(**results[0])
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get promo code by code", context)
            raise

    async def list_promo_codes(
        self, active_only: bool = True, correlation_id: str = ""
    ) -> list[PromoCodeResponse]:
        """List all promo codes."""
        context = {
            "operation": "list_promo_codes",
            "component": "PromoService",
            "correlation_id": correlation_id,
            "active_only": active_only,
        }
        self.logger.log_info("Listing promo codes", context)

        try:
            filters = {"is_active": True} if active_only else {}
            results = self.supabase.query_records(
                "promo_codes", filters=filters, correlation_id=correlation_id
            )
            return [PromoCodeResponse(**result) for result in results]
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list promo codes", context)
            raise

    async def update_promo_code(
        self, promo_id: UUID, data: PromoCodeUpdate, correlation_id: str
    ) -> PromoCodeResponse:
        """Update promo code."""
        context = {
            "operation": "update_promo_code",
            "component": "PromoService",
            "correlation_id": correlation_id,
            "promo_id": str(promo_id),
        }
        self.logger.log_info("Updating promo code", context)

        try:
            # Check if promo code exists
            existing = self.supabase.get_record(
                "promo_codes", str(promo_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Promo code not found", correlation_id)

            # Update promo code
            update_data = data.model_dump(exclude_unset=True)
            result = self.supabase.update_record(
                "promo_codes", str(promo_id), update_data, correlation_id=correlation_id
            )

            self.logger.log_info("Promo code updated successfully", context)
            return PromoCodeResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update promo code", context)
            raise

    async def validate_promo_code(
        self, data: PromoValidateRequest, user_id: UUID, correlation_id: str
    ) -> PromoValidateResponse:
        """
        Validate a promo code for a specific plan.

        Args:
            data: Promo validation request
            user_id: ID of the user validating the promo
            correlation_id: Request correlation ID

        Returns:
            Promo validation response with discount details
        """
        context = {
            "operation": "validate_promo_code",
            "component": "PromoService",
            "correlation_id": correlation_id,
            "code": data.code,
            "plan_id": str(data.plan_id),
        }
        self.logger.log_info("Validating promo code", context)

        try:
            # Get promo code
            promo = await self.get_promo_code_by_code(data.code, correlation_id)
            if not promo:
                return PromoValidateResponse(
                    valid=False,
                    message="Promo code not found",
                )

            # Check if promo is active
            if not promo.is_active:
                return PromoValidateResponse(
                    valid=False,
                    message="Promo code is not active",
                )

            # Check expiration
            if promo.expires_at:
                if promo.expires_at <= datetime.now(timezone.utc):
                    return PromoValidateResponse(
                        valid=False,
                        message="Promo code has expired",
                    )

            # Check usage limit
            if promo.max_uses is not None:
                if promo.current_uses >= promo.max_uses:
                    return PromoValidateResponse(
                        valid=False,
                        message="Promo code usage limit reached",
                    )

            # Check if user already used this promo
            usages = self.supabase.query_records(
                "promo_usages",
                filters={"promo_code_id": str(promo.id), "user_id": str(user_id)},
                correlation_id=correlation_id,
            )
            if usages:
                return PromoValidateResponse(
                    valid=False,
                    message="You have already used this promo code",
                )

            # Get plan details
            plan = self.supabase.get_record(
                "plans", str(data.plan_id), correlation_id=correlation_id
            )
            if not plan:
                return PromoValidateResponse(
                    valid=False,
                    message="Plan not found",
                )

            plan_price = Decimal(str(plan["price"]))

            # Calculate discount
            final_price = await self.calculate_discount(
                plan_price, promo.type, promo.value
            )

            self.logger.log_info("Promo code validated successfully", context)
            return PromoValidateResponse(
                valid=True,
                discount_type=promo.type,
                discount_value=promo.value,
                final_price=final_price,
                message="Promo code is valid",
            )
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to validate promo code", context)
            raise

    async def calculate_discount(
        self, original_price: Decimal, discount_type: DiscountType, discount_value: Decimal
    ) -> Decimal:
        """Calculate final price after discount."""
        if discount_type == DiscountType.PERCENTAGE:
            discount_amount = original_price * (discount_value / 100)
            final_price = original_price - discount_amount
        else:  # FIXED
            final_price = original_price - discount_value

        # Ensure price doesn't go below zero
        return max(Decimal("0"), final_price)

    async def apply_promo_code(
        self, promo_code: str, user_id: UUID, subscription_id: UUID, correlation_id: str
    ) -> None:
        """
        Apply promo code and record usage.

        Args:
            promo_code: Promo code string
            user_id: ID of the user using the promo
            subscription_id: ID of the subscription
            correlation_id: Request correlation ID

        Raises:
            ValidationError: If promo code is invalid
        """
        context = {
            "operation": "apply_promo_code",
            "component": "PromoService",
            "correlation_id": correlation_id,
            "code": promo_code,
            "user_id": str(user_id),
            "subscription_id": str(subscription_id),
        }
        self.logger.log_info("Applying promo code", context)

        try:
            # Get promo code
            promo = await self.get_promo_code_by_code(promo_code, correlation_id)
            if not promo:
                raise ValidationError("Promo code not found", correlation_id)

            # Create promo usage record
            self.supabase.insert_record(
                "promo_usages",
                {
                    "promo_code_id": str(promo.id),
                    "user_id": str(user_id),
                    "subscription_id": str(subscription_id),
                },
                correlation_id=correlation_id,
            )

            # Increment current_uses
            new_current_uses = promo.current_uses + 1
            self.supabase.update_record(
                "promo_codes",
                str(promo.id),
                {"current_uses": new_current_uses},
                correlation_id=correlation_id,
            )

            self.logger.log_info("Promo code applied successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to apply promo code", context)
            raise
