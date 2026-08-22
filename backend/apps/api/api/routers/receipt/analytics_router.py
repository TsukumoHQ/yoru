"""Token-usage analytics — GET /analytics/tokens (9be89019), GET
/analytics/tokens/sessions (8c5d82ad).

Data for the founder's stats-token panel: time-bucketed (day/week) rollups
of tokens_input/tokens_output/cost_usd, already stored per-session
(`Session.tokens_input/output/cost_usd`) but never aggregated with a range
or bucket before this. Org scope also drills by dev (full roster) and by
project (VCS provider only — see TokenAnalyticsProject); /tokens/sessions
adds the by-session grain (8c5d82ad, CTO org-restructure cost-control
directive 2026-08-21).

RBAC (interim guard, cto-tsukumo, ahead of the d2cf7c71 design ruling):
  - Own-scope (the caller's own usage) is ALWAYS returned — a dev can always
    see their own numbers.
  - Org-wide totals/series/top-spenders are returned ONLY when the caller
    passes `require_org_admin` for the requested `org_id` — a dev must never
    be able to pull a colleague's usage by curling this endpoint. Self-host
    (AUTH_PROVIDER=local) is single-tenant: any authenticated dashboard user
    is authorized (README: "the first registered user becomes the admin"),
    matching the existing service-token admin gate's semantics exactly —
    this endpoint reuses that same check, not a new one.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session as SQLSession
from sqlmodel import select

from apps.api.api.dependencies.auth import (
    get_current_user_id,
    get_current_user_token,
)
from apps.api.api.services.auth.provider import get_auth_provider

from . import dashboard_router
from . import vcs as vcs_registry
from .auth_router import require_org_admin
from .db import get_session
from .models import Session as SessionRow
from .models import (
    TokenAnalyticsBucket,
    TokenAnalyticsOrgScope,
    TokenAnalyticsOut,
    TokenAnalyticsProject,
    TokenAnalyticsScope,
    TokenAnalyticsSessionItem,
    TokenAnalyticsSessionsOut,
    TokenAnalyticsSpender,
    TokenAnalyticsTotals,
)

_TOP_SPENDERS_LIMIT = 10
_SESSIONS_LIMIT_DEFAULT = 50
_SESSIONS_LIMIT_MAX = 200


def _naive_utc(d: datetime) -> datetime:
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def _bucket_start(dt: datetime, bucket: Literal["day", "week"]) -> datetime:
    """Truncate to the start of the bucket `dt` falls in. "day" -> midnight
    of that date; "week" -> Monday (ISO week start) of that date's week."""
    d = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "week":
        d = d - timedelta(days=d.weekday())
    return d


class _Row:
    """One session's usage. `vcs` is derived from git_remote at fetch time and
    the raw remote is discarded immediately — same provider-only exposure as
    SessionListItem.vcs (models.py), never held as a field or returned as-is."""

    __slots__ = ("id", "user", "started_at", "tokens_input", "tokens_output", "cost_usd", "vcs")

    def __init__(
        self,
        id: str,
        user: str,
        started_at: datetime,
        tokens_input: int,
        tokens_output: int,
        cost_usd: float,
        git_remote: Optional[str],
    ):
        self.id = id
        self.user = user
        self.started_at = started_at
        self.tokens_input = tokens_input
        self.tokens_output = tokens_output
        self.cost_usd = cost_usd
        self.vcs = vcs_registry.provider_of_remote(git_remote)


def _fetch_rows(db: SQLSession, scope_filter, since_dt: datetime, until_dt: datetime) -> list[_Row]:
    stmt = (
        select(
            SessionRow.id,
            SessionRow.user,
            SessionRow.started_at,
            SessionRow.tokens_input,
            SessionRow.tokens_output,
            SessionRow.cost_usd,
            SessionRow.git_remote,
        )
        .where(SessionRow.started_at >= since_dt)
        .where(SessionRow.started_at < until_dt)
        .where(scope_filter)
    )
    return [_Row(*r) for r in db.exec(stmt).all()]


def _totals(rows: list[_Row]) -> TokenAnalyticsTotals:
    return TokenAnalyticsTotals(
        tokens_input=sum(r.tokens_input for r in rows),
        tokens_output=sum(r.tokens_output for r in rows),
        cost_usd=sum(r.cost_usd for r in rows),
    )


def _series(rows: list[_Row], bucket: Literal["day", "week"]) -> list[TokenAnalyticsBucket]:
    buckets: dict[datetime, list[_Row]] = defaultdict(list)
    for r in rows:
        buckets[_bucket_start(r.started_at, bucket)].append(r)
    return [
        TokenAnalyticsBucket(
            bucket_start=start,
            tokens_input=sum(r.tokens_input for r in bucket_rows),
            tokens_output=sum(r.tokens_output for r in bucket_rows),
            cost_usd=sum(r.cost_usd for r in bucket_rows),
        )
        for start, bucket_rows in sorted(buckets.items())
    ]


def _by_dev(rows: list[_Row]) -> list[TokenAnalyticsSpender]:
    """Every dev with usage in range, sorted highest spend first. `top_spenders`
    is this same list capped to `_TOP_SPENDERS_LIMIT` (kept separate for
    back-compat with the existing Exceptions panel contract)."""
    per_user: dict[str, list[_Row]] = defaultdict(list)
    for r in rows:
        per_user[r.user].append(r)
    spenders = [
        TokenAnalyticsSpender(
            user=user,
            tokens_input=sum(r.tokens_input for r in user_rows),
            tokens_output=sum(r.tokens_output for r in user_rows),
            cost_usd=sum(r.cost_usd for r in user_rows),
        )
        for user, user_rows in per_user.items()
    ]
    spenders.sort(key=lambda s: s.cost_usd, reverse=True)
    return spenders


def _by_project(rows: list[_Row]) -> list[TokenAnalyticsProject]:
    per_vcs: dict[Optional[str], list[_Row]] = defaultdict(list)
    for r in rows:
        per_vcs[r.vcs].append(r)
    projects = [
        TokenAnalyticsProject(
            vcs=vcs,
            tokens_input=sum(r.tokens_input for r in vcs_rows),
            tokens_output=sum(r.tokens_output for r in vcs_rows),
            cost_usd=sum(r.cost_usd for r in vcs_rows),
        )
        for vcs, vcs_rows in per_vcs.items()
    ]
    projects.sort(key=lambda p: p.cost_usd, reverse=True)
    return projects


def _resolve_caller_email(user_id: UUID, token: str) -> Optional[str]:
    """Same caller-email resolution as /dashboard/team: prefer the Supabase
    `profiles` row, fall back to the verified JWT's email (the self-host
    path — no Supabase profile to look up)."""
    _workspace_ids, profile_email = dashboard_router._resolve_scope(user_id)
    if profile_email:
        return profile_email
    try:
        return get_auth_provider().email_from_token(token)
    except Exception:
        return None


class AnalyticsRouter:
    """GET /analytics/tokens — time-bucketed token/cost rollups.
    GET /analytics/tokens/sessions — by-session drill (cost-control)."""

    def __init__(self) -> None:
        self.router = APIRouter(prefix="/analytics", tags=["receipt:analytics"])
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.get("/tokens", response_model=TokenAnalyticsOut)(self.tokens)
        self.router.get("/tokens/sessions", response_model=TokenAnalyticsSessionsOut)(self.tokens_sessions)

    def tokens(
        self,
        request: Request,
        from_: Optional[datetime] = Query(default=None, alias="from"),
        to: Optional[datetime] = Query(default=None),
        bucket: Literal["day", "week"] = Query(default="day"),
        org_id: Optional[str] = Query(
            default=None,
            description="Request the org-wide rollup + top-spenders too — "
            "requires org-admin (or, on self-host, any dashboard session).",
        ),
        db: SQLSession = Depends(get_session),
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> TokenAnalyticsOut:
        until_dt = _naive_utc(to) if to is not None else _naive_utc(datetime.now(timezone.utc))
        since_dt = _naive_utc(from_) if from_ is not None else (until_dt - timedelta(days=7))

        caller_email = _resolve_caller_email(user_id, token)

        own_rows = (
            _fetch_rows(db, SessionRow.user == caller_email, since_dt, until_dt)
            if caller_email
            else []
        )
        own = TokenAnalyticsScope(totals=_totals(own_rows), series=_series(own_rows, bucket))

        org: Optional[TokenAnalyticsOrgScope] = None
        if org_id:
            require_org_admin(request, org_id)
            org_rows = _fetch_rows(db, SessionRow.org_id == org_id, since_dt, until_dt)
            dev_spenders = _by_dev(org_rows)
            org = TokenAnalyticsOrgScope(
                totals=_totals(org_rows),
                series=_series(org_rows, bucket),
                top_spenders=dev_spenders[:_TOP_SPENDERS_LIMIT],
                by_dev=dev_spenders,
                by_project=_by_project(org_rows),
            )

        return TokenAnalyticsOut(
            bucket=bucket,
            since=since_dt,
            until=until_dt,
            own=own,
            org=org,
        )

    def tokens_sessions(
        self,
        request: Request,
        from_: Optional[datetime] = Query(default=None, alias="from"),
        to: Optional[datetime] = Query(default=None),
        org_id: Optional[str] = Query(
            default=None,
            description="Drill org-wide sessions — requires org-admin. Omit for "
            "the caller's own sessions (always allowed).",
        ),
        dev: Optional[str] = Query(default=None, description="Filter to one dev's email (org scope only)."),
        project: Optional[str] = Query(
            default=None, description="Filter to one VCS provider slug (github|gitlab|bitbucket|azure)."
        ),
        limit: int = Query(default=_SESSIONS_LIMIT_DEFAULT, ge=1, le=_SESSIONS_LIMIT_MAX),
        db: SQLSession = Depends(get_session),
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> TokenAnalyticsSessionsOut:
        until_dt = _naive_utc(to) if to is not None else _naive_utc(datetime.now(timezone.utc))
        since_dt = _naive_utc(from_) if from_ is not None else (until_dt - timedelta(days=7))

        if org_id:
            require_org_admin(request, org_id)
            rows = _fetch_rows(db, SessionRow.org_id == org_id, since_dt, until_dt)
            if dev:
                rows = [r for r in rows if r.user == dev]
        else:
            # Own-scope drill: always allowed, never gated — same invariant as
            # /analytics/tokens's bare `own`. `dev` is meaningless without an
            # org (the caller can only ever see their own rows here).
            caller_email = _resolve_caller_email(user_id, token)
            rows = _fetch_rows(db, SessionRow.user == caller_email, since_dt, until_dt) if caller_email else []

        if project:
            rows = [r for r in rows if r.vcs == project]

        rows.sort(key=lambda r: r.cost_usd, reverse=True)
        total = len(rows)
        items = [
            TokenAnalyticsSessionItem(
                id=r.id,
                user=r.user,
                vcs=r.vcs,
                started_at=r.started_at,
                tokens_input=r.tokens_input,
                tokens_output=r.tokens_output,
                cost_usd=r.cost_usd,
            )
            for r in rows[:limit]
        ]
        return TokenAnalyticsSessionsOut(items=items, total=total)
