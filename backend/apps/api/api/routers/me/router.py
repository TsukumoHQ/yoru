"""Me router for current user operations."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from libs.log_manager.controller import LoggingController
from apps.api.api.dependencies.auth import (
    get_correlation_id,
    get_current_user_id,
    get_current_user_token,
)
from apps.api.api.models.user.user_models import UserResponse, UserUpdate
from apps.api.api.models.subscription.subscription_models import SubscriptionResponse
from apps.api.api.models.subscription.grant_models import GrantResponse
from apps.api.api.models.group.group_models import (
    UserGroupResponse,
    GroupFeatureResponse,
)
from apps.api.api.services.user.user_service import UserService
from apps.api.api.services.subscription.subscription_service import SubscriptionService
from apps.api.api.services.subscription.grant_service import GrantService
from apps.api.api.services.group.group_service import UserGroupService

from .route_rules_endpoints import (
    RouteRuleIn,
    RouteRuleOut,
    create_route_rule,
    delete_route_rule,
    list_route_rules,
)
from .workspaces_endpoints import (
    PromoteIn,
    WorkspaceIn,
    WorkspaceOut,
    WorkspacePatch,
    WorkspaceRepoIn,
    WorkspaceRepoOut,
    add_workspace_repo,
    create_workspace,
    delete_workspace,
    list_workspace_repos,
    list_workspaces,
    promote_workspace,
    remove_workspace_repo,
    update_workspace,
)
from .github_endpoints import (
    AutoRouteIn,
    GithubConnectIn,
    GithubRepoOut,
    GithubStatusOut,
    auto_route_repos,
    connect_github,
    disconnect_github,
    get_github_status,
    list_github_repos,
)


class MeRouter:
    """Router for current user endpoints."""

    def __init__(self):
        self.logger = LoggingController(app_name="MeRouter")
        self.router = APIRouter(prefix="/me", tags=["me"])
        self._setup_routes()

    def initialize_services(self):
        """Initialize required services - not used anymore as services are created per-request."""
        pass

    def get_router(self) -> APIRouter:
        """Get the FastAPI router."""
        return self.router

    def _setup_routes(self):
        """Set up me routes."""
        self.router.get(
            "",
            response_model=UserResponse,
            status_code=200,
            summary="Get current user profile",
        )(self.get_me)

        self.router.patch(
            "",
            response_model=UserResponse,
            status_code=200,
            summary="Update current user profile",
        )(self.update_me)

        self.router.get(
            "/subscription",
            response_model=SubscriptionResponse | None,
            status_code=200,
            summary="Get current user's subscription",
        )(self.get_my_subscription)

        self.router.get(
            "/grants",
            response_model=list[GrantResponse],
            status_code=200,
            summary="Get current user's grants",
        )(self.get_my_grants)

        self.router.get(
            "/groups",
            response_model=list[UserGroupResponse],
            status_code=200,
            summary="Get current user's groups",
        )(self.get_my_groups)

        self.router.get(
            "/groups/features",
            response_model=list[GroupFeatureResponse],
            status_code=200,
            summary="Get features accessible via user's groups",
        )(self.get_my_group_features)

        self.router.get(
            "/route-rules",
            response_model=list[RouteRuleOut],
            status_code=200,
            summary="List caller's routing rules",
        )(self.list_my_route_rules)
        self.router.post(
            "/route-rules",
            response_model=RouteRuleOut,
            status_code=201,
            summary="Create a user-scope routing rule",
        )(self.create_my_route_rule)
        self.router.delete(
            "/route-rules/{rule_id}",
            status_code=204,
            response_model=None,
            summary="Delete a user-scope routing rule",
        )(self.delete_my_route_rule)

        # Workspaces (Phase W2)
        self.router.get(
            "/workspaces",
            response_model=list[WorkspaceOut],
            status_code=200,
            summary="List caller's workspaces (perso + team via org membership)",
        )(self.list_my_workspaces)
        self.router.post(
            "/workspaces",
            response_model=WorkspaceOut,
            status_code=201,
            summary="Create a workspace (enforces plan workspaces_max for personal)",
        )(self.create_my_workspace)
        self.router.patch(
            "/workspaces/{workspace_id}",
            response_model=WorkspaceOut,
            status_code=200,
            summary="Rename / update settings on a workspace",
        )(self.update_my_workspace)
        self.router.delete(
            "/workspaces/{workspace_id}",
            status_code=204,
            response_model=None,
            summary="Soft-delete a workspace",
        )(self.delete_my_workspace)
        self.router.get(
            "/workspaces/{workspace_id}/repos",
            response_model=list[WorkspaceRepoOut],
            status_code=200,
            summary="List repos mapped to a workspace",
        )(self.list_my_workspace_repos)
        self.router.post(
            "/workspaces/{workspace_id}/repos",
            response_model=WorkspaceRepoOut,
            status_code=201,
            summary="Map a repo to a workspace",
        )(self.add_my_workspace_repo)
        self.router.delete(
            "/workspaces/{workspace_id}/repos/{repo_id}",
            status_code=204,
            response_model=None,
            summary="Unmap a repo from a workspace",
        )(self.remove_my_workspace_repo)
        self.router.post(
            "/workspaces/{workspace_id}/promote",
            status_code=201,
            summary="Promote a personal workspace to a new organization",
        )(self.promote_my_workspace)

        # Feature resolution (Phase W6) — lets the frontend read a single
        # feature's effective value (grant → plan → default).
        self.router.get(
            "/features/{feature_key}",
            status_code=200,
            summary="Resolve a feature's effective value for the current user",
        )(self.get_my_feature)

        # GitHub integration (Phase W3)
        self.router.get(
            "/github",
            response_model=GithubStatusOut,
            status_code=200,
            summary="Status of the user's GitHub integration",
        )(self.get_my_github_status)
        self.router.post(
            "/github/connect",
            response_model=GithubStatusOut,
            status_code=201,
            summary="Persist a provider_token from the Supabase GitHub OAuth callback",
        )(self.connect_my_github)
        self.router.delete(
            "/github",
            status_code=204,
            response_model=None,
            summary="Disconnect GitHub for the caller",
        )(self.disconnect_my_github)
        self.router.get(
            "/github/repos",
            response_model=list[GithubRepoOut],
            status_code=200,
            summary="List the user's GitHub repos (paginated)",
        )(self.list_my_github_repos)
        self.router.post(
            "/github/auto-route",
            status_code=200,
            summary="Bulk-assign a list of GitHub repos to a workspace",
        )(self.auto_route_my_github_repos)

    async def get_me(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> UserResponse:
        """
        Get the current user's profile.

        Requires authentication via Bearer token.
        """
        user_service = UserService(access_token=token)
        return await user_service.get_by_id(user_id, correlation_id)

    async def update_me(
        self,
        request: Request,
        data: UserUpdate,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> UserResponse:
        """
        Update the current user's profile.

        Only provided fields will be updated.
        Requires authentication via Bearer token.
        """
        user_service = UserService(access_token=token)
        return await user_service.update(user_id, data, correlation_id)

    async def get_my_subscription(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> SubscriptionResponse | None:
        """
        Get the current user's active subscription.

        Returns None if user has no active subscription.
        Requires authentication via Bearer token.
        """
        # Pass the caller's JWT so RLS on `user_subscription_details` resolves
        # to their own row. Without it, SupabaseManager falls back to the
        # anon key and the view returns 0 rows under security_invoker=true.
        subscription_service = SubscriptionService(access_token=token)
        return await subscription_service.get_user_active_subscription(
            user_id, correlation_id
        )

    async def get_my_grants(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        correlation_id: str = Depends(get_correlation_id),
    ) -> list[GrantResponse]:
        """
        Get the current user's active grants.

        Returns list of grants that override subscription features.
        Requires authentication via Bearer token.
        """
        grant_service = GrantService()
        return await grant_service.get_active_user_grants(user_id, correlation_id)

    async def get_my_groups(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> list[UserGroupResponse]:
        """
        Get all groups the current user belongs to.

        Returns list of active groups with member counts.
        Requires authentication via Bearer token.
        """
        group_service = UserGroupService(access_token=token)
        return await group_service.get_user_groups(user_id, correlation_id)

    async def get_my_group_features(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
        correlation_id: str = Depends(get_correlation_id),
    ) -> list[GroupFeatureResponse]:
        """
        Get all features accessible to the current user via their group memberships.

        Returns list of features with their values, organized by group.
        Requires authentication via Bearer token.
        """
        group_service = UserGroupService(access_token=token)
        return await group_service.get_user_features_via_groups(user_id, correlation_id)

    # ---------- Route rules (Phase D-lite) ----------

    async def list_my_route_rules(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> list[RouteRuleOut]:
        return await list_route_rules(token, user_id)

    async def create_my_route_rule(
        self,
        request: Request,
        body: RouteRuleIn,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> RouteRuleOut:
        return await create_route_rule(token, user_id, body)

    async def delete_my_route_rule(
        self,
        request: Request,
        rule_id: str,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> None:
        return await delete_route_rule(token, rule_id, user_id)

    # ---------- Workspaces (Phase W2) ----------

    async def list_my_workspaces(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> list[WorkspaceOut]:
        return await list_workspaces(token, user_id)

    async def create_my_workspace(
        self,
        request: Request,
        body: WorkspaceIn,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> WorkspaceOut:
        return await create_workspace(token, user_id, body)

    async def update_my_workspace(
        self,
        request: Request,
        workspace_id: str,
        body: WorkspacePatch,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> WorkspaceOut:
        return await update_workspace(token, workspace_id, body, user_id)

    async def delete_my_workspace(
        self,
        request: Request,
        workspace_id: str,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> None:
        return await delete_workspace(token, workspace_id, user_id)

    async def list_my_workspace_repos(
        self,
        request: Request,
        workspace_id: str,
        token: str = Depends(get_current_user_token),
    ) -> list[WorkspaceRepoOut]:
        return await list_workspace_repos(token, workspace_id)

    async def add_my_workspace_repo(
        self,
        request: Request,
        workspace_id: str,
        body: WorkspaceRepoIn,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> WorkspaceRepoOut:
        return await add_workspace_repo(token, user_id, workspace_id, body)

    async def remove_my_workspace_repo(
        self,
        request: Request,
        workspace_id: str,
        repo_id: str,
        token: str = Depends(get_current_user_token),
    ) -> None:
        return await remove_workspace_repo(token, workspace_id, repo_id)

    async def promote_my_workspace(
        self,
        request: Request,
        workspace_id: str,
        body: PromoteIn,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> dict:
        return await promote_workspace(token, user_id, workspace_id, body)

    # ---------- Features (Phase W6) ----------

    async def get_my_feature(
        self,
        request: Request,
        feature_key: str,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> dict:
        """Resolve a feature key's effective value for the current user.

        Resolution order (via SupabaseManager + Yoru tables):
            1. user_grants (admin override, with optional expiry)
            2. active subscription → plan_features
            3. features.default_value
        Returns {key, value, source, type}. `source` is one of
        'grant' | 'plan' | 'default' | 'unknown'.
        """
        from datetime import datetime, timezone

        from libs.datastore import get_data_store

        sb = get_data_store(access_token=token)
        feats = sb.query_records("features", filters={"key": feature_key})
        if not feats:
            return {"key": feature_key, "value": None, "source": "unknown", "type": None}
        feat = feats[0]
        feat_type = feat.get("type")

        # 1. user_grants
        grants = sb.query_records(
            "user_grants",
            filters={
                "user_id": str(user_id),
                "feature_id": feat["id"],
            },
        )
        now = datetime.now(timezone.utc)
        for g in grants or []:
            exp = g.get("expires_at")
            if exp:
                try:
                    if datetime.fromisoformat(exp.replace("Z", "+00:00")) < now:
                        continue
                except ValueError:
                    pass
            return {"key": feature_key, "value": g.get("value"), "source": "grant", "type": feat_type}

        # 2. active subscription → plan_features
        subs = sb.query_records(
            "subscriptions",
            filters={"user_id": str(user_id), "status": "active"},
        )
        if subs:
            plan_id = subs[0].get("plan_id")
            if plan_id:
                pfs = sb.query_records(
                    "plan_features",
                    filters={"plan_id": plan_id, "feature_id": feat["id"]},
                )
                if pfs:
                    return {"key": feature_key, "value": pfs[0].get("value"), "source": "plan", "type": feat_type}

        # 3. default
        return {"key": feature_key, "value": feat.get("default_value"), "source": "default", "type": feat_type}

    # ---------- GitHub integration (Phase W3) ----------

    async def get_my_github_status(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> GithubStatusOut:
        return await get_github_status(token, user_id)

    async def connect_my_github(
        self,
        request: Request,
        body: GithubConnectIn,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> GithubStatusOut:
        return await connect_github(token, user_id, body)

    async def disconnect_my_github(
        self,
        request: Request,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> None:
        return await disconnect_github(token, user_id)

    async def list_my_github_repos(
        self,
        request: Request,
        per_page: int = 100,
        page: int = 1,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> list[GithubRepoOut]:
        return await list_github_repos(token, user_id, per_page=per_page, page=page)

    async def auto_route_my_github_repos(
        self,
        request: Request,
        body: AutoRouteIn,
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> dict:
        return await auto_route_repos(token, user_id, body)
