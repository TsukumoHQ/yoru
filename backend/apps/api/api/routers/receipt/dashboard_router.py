"""Team dashboard router.

Contract: task api-team-dashboard-endpoint (PRODUCT.md §6). Aggregates
sessions per user within a time window.

Auth: cookie or bearer JWT via `get_current_user_id` (dashboard flow).
Pre-hardening this was unauthenticated and leaked every user's email +
session count + cost to anyone — fixed 2026-04-23.

Scope: the caller sees users active in workspaces the caller is a member
of — derived from Supabase (not from SQLite session activity). Membership
rules:
  - Personal workspace: `workspaces.owner_user_id = caller AND org_id IS NULL`
  - Org workspace:      `workspaces.org_id IN (orgs where caller is a member)`

Using Supabase as the source of truth means a teammate who just joined
your org shows up immediately — they don't need to run a session first.

Self-host: there is no Supabase, so the membership lookups return nothing and
`caller_email` falls back to the email in the verified JWT. The caller then
sees their own sessions (workspace_ids = []). A local instance with no org is
the normal case, and this is what makes the dashboard non-empty there.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlmodel import Session as SQLSession
from sqlmodel import select

from apps.api.api.dependencies.auth import (
    get_current_user_id,
    get_current_user_token,
)
from apps.api.api.services.auth.provider import get_auth_provider
from libs.datastore import get_data_store
from libs.log_manager.controller import LoggingController

from .db import get_session
from .models import (
    Session as SessionRow,
)
from .models import (
    TeamDashboardOut,
    TeamDashboardTotals,
    TeamDashboardUser,
)


def _naive_utc(d: datetime) -> datetime:
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def _default_since() -> datetime:
    return _naive_utc(datetime.now(timezone.utc) - timedelta(days=7))


def _caller_email_from_token(token: str) -> Optional[str]:
    """Caller's email straight from the verified JWT claim.

    Self-host fallback: `_resolve_scope` reads the email from the Supabase
    `profiles` table, which is empty on a local store, so it returns None and
    the dashboard would scope to nothing. The email is already in the access
    token both providers issue — read it directly. Works identically on cloud.
    """
    try:
        return get_auth_provider().email_from_token(token)
    except Exception:
        return None


def _resolve_scope(user_id: UUID) -> tuple[list[str], Optional[str]]:
    """Resolve the caller's workspace-membership scope.

    Returns (workspace_ids, caller_email):
      - workspace_ids: every workspace where the caller is owner (personal)
        or a member of the owning organization (team).
      - caller_email: the email recorded on auth.users, used to include the
        caller's own legacy (NULL-workspace) sessions in the aggregate.

    Uses the service-role Supabase client — the caller's identity has
    already been verified by `get_current_user_id`, and we only need to
    read trusted membership rows, no user-specific RLS scoping needed.
    """
    logger = LoggingController(app_name="DashboardRouter")
    sb = get_data_store(enable_cache=False).client
    user_id_str = str(user_id)

    caller_email: Optional[str] = None
    try:
        profile = (
            sb.table("profiles")
            .select("email")
            .eq("id", user_id_str)
            .limit(1)
            .execute()
        )
        if profile.data:
            caller_email = profile.data[0].get("email")
    except Exception as exc:
        # Non-fatal — aggregate still filters by workspace_ids. We just won't
        # include legacy NULL-workspace rows for this caller.
        logger.log_warning(
            "Failed to resolve caller email from profiles", {"error": str(exc)}
        )

    try:
        orgs_rows = (
            sb.table("organization_members")
            .select("org_id")
            .eq("user_id", user_id_str)
            .execute()
        )
        my_org_ids = [r["org_id"] for r in (orgs_rows.data or []) if r.get("org_id")]
    except Exception as exc:
        logger.log_warning(
            "Failed to list organization_members for caller",
            {"error": str(exc)},
        )
        my_org_ids = []

    workspace_ids: list[str] = []
    try:
        personal = (
            sb.table("workspaces")
            .select("id")
            .eq("owner_user_id", user_id_str)
            .is_("org_id", "null")
            .is_("deleted_at", "null")
            .execute()
        )
        workspace_ids.extend(r["id"] for r in (personal.data or []) if r.get("id"))

        if my_org_ids:
            team = (
                sb.table("workspaces")
                .select("id")
                .in_("org_id", my_org_ids)
                .is_("deleted_at", "null")
                .execute()
            )
            workspace_ids.extend(r["id"] for r in (team.data or []) if r.get("id"))
    except Exception as exc:
        logger.log_warning(
            "Failed to resolve workspaces for caller", {"error": str(exc)}
        )

    return workspace_ids, caller_email


class DashboardRouter:
    """GET /dashboard/team — per-user session aggregates."""

    def __init__(self) -> None:
        self.router = APIRouter(prefix="/dashboard", tags=["receipt:dashboard"])
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.get("/team", response_model=TeamDashboardOut)(self.team)

    def team(
        self,
        since: Optional[datetime] = Query(default=None),
        db: SQLSession = Depends(get_session),
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> TeamDashboardOut:
        since_dt = _naive_utc(since) if since is not None else _default_since()

        workspace_ids, profile_email = _resolve_scope(user_id)
        # Prefer the email from the verified JWT — it's always present and makes
        # the dashboard work on self-host, where _resolve_scope's Supabase
        # `profiles` lookup returns nothing. On cloud both agree.
        caller_email = profile_email or _caller_email_from_token(token)

        # Always include the caller themselves by email so legacy rows with
        # workspace_id = NULL still show up for their owner, and so a brand-new
        # user with zero org memberships (the normal self-host case) still sees
        # their own activity.
        scope_filter = SessionRow.user == caller_email
        if workspace_ids:
            scope_filter = scope_filter | SessionRow.workspace_id.in_(workspace_ids)

        group_stmt = (
            select(
                SessionRow.user,
                func.count().label("sessions"),
                func.coalesce(func.sum(SessionRow.flagged), 0).label("flagged"),
                func.coalesce(func.sum(SessionRow.cost_usd), 0.0).label(
                    "total_cost_usd"
                ),
            )
            .where(SessionRow.started_at >= since_dt)
            .where(scope_filter)
            .group_by(SessionRow.user)
            .order_by(SessionRow.user)
        )
        rows = db.exec(group_stmt).all()

        users = [
            TeamDashboardUser(
                email=r.user,
                sessions=int(r.sessions),
                flagged=int(r.flagged or 0),
                total_cost_usd=float(r.total_cost_usd or 0.0),
            )
            for r in rows
        ]
        total_sessions = sum(u.sessions for u in users)
        total_flagged = sum(u.flagged for u in users)
        flagged_pct = (total_flagged / total_sessions) if total_sessions else 0.0

        return TeamDashboardOut(
            users=users,
            totals=TeamDashboardTotals(
                sessions=total_sessions,
                flagged=total_flagged,
                flagged_pct=flagged_pct,
            ),
        )
