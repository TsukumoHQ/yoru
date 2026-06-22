"""Feature access dependencies.

Resolution order (highest priority first):
  1. user_grants          — admin overrides with optional expiry
  2. subscription → plan_features — what the user's active plan grants
  3. features.default_value       — fallback for unplanned users

The Yoru feature-check middleware (`middleware/feature_check.py`) was
designed to read `request.state.required_feature` set by this dep, but
`BaseHTTPMiddleware.dispatch()` runs before route dependencies resolve, so
the state is always empty when the middleware reads it. We do the check
inline in the dep instead — one fewer moving part, works.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import Depends, HTTPException, Request

from apps.api.api.dependencies.auth import (
    get_correlation_id,
    get_current_user_id,
    get_current_user_token,
)
from apps.api.api.services.subscription.subscription_service import SubscriptionService


def require_feature(feature_key: str):
    """Require the caller's active subscription (+grants) to expose `feature_key`.

    Flag features: resolves to truthy → allow, falsy → 403.
    Quota features: presence alone grants; actual quota consumption is the
    endpoint's responsibility. Use `resolve_feature_value` when you need the
    numeric limit (e.g. `events_per_month`).

    Usage:
        @router.post("/events")
        async def ingest(
            _: None = Depends(require_feature("outbound_webhooks_max")),
            ...
        ):
            ...
    """

    async def dep(
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> None:
        # Pass caller JWT so the subscription lookup clears RLS on
        # `user_subscription_details` (security_invoker=true view).
        service = SubscriptionService(access_token=token)
        try:
            has_access = await service.check_feature_access(
                user_id, feature_key, correlation_id
            )
        except Exception as err:
            raise HTTPException(
                status_code=500,
                detail=f"Feature check failed: {err}",
            ) from err
        if not has_access:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Feature '{feature_key}' not available on your plan. "
                    "Upgrade to unlock."
                ),
            )
        request.state.required_feature = feature_key

    return Depends(dep)
