"""Red-flag exceptions analytics — GET /analytics/exceptions (5a72353b).

CTO-view counterpart to the token-analytics endpoint (9be89019): an
exception-first rollup of `EventFlag` (the queryable red-flag index, see
`models.py::EventFlag`) — flagged-session counts, top triggered rule_ids,
and (org-wide) top offenders — instead of a raw dump of every flag.

RBAC (same shape as 9be89019, ruled DEC-yoru-rbac-ruling-1):
  - Own-scope (the caller's own flags) is ALWAYS returned — a dev can always
    see their own exceptions.
  - Org-wide totals/top-rule-ids/top-offenders are returned ONLY when the
    caller passes `require_org_admin` for the requested `org_id` — a dev
    must never be able to pull a colleague's exception history by curling
    this endpoint. Reuses `require_org_admin` verbatim (self-host: any
    authenticated dashboard user; cloud: org owner/admin OR studio
    super-admin per DEC-yoru-rbac-ruling-1 Q1). Enforced server-side via the
    dashboard cookie vector only (`require_org_admin` → `require_dashboard_jwt`
    rejects a CliToken bearer) — never client-supplied, never satisfiable by
    a hook token.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
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
from .auth_router import require_org_admin
from .db import get_session
from .models import EventFlag
from .models import Session as SessionRow
from .models import (
    ExceptionAnalyticsOffender,
    ExceptionAnalyticsOrgScope,
    ExceptionAnalyticsOut,
    ExceptionAnalyticsRuleCount,
    ExceptionAnalyticsScope,
    ExceptionAnalyticsTotals,
)

_TOP_RULE_IDS_LIMIT = 10
_TOP_OFFENDERS_LIMIT = 10


def _naive_utc(d: datetime) -> datetime:
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


class _FlagRow:
    __slots__ = ("rule_id", "session_id", "user")

    def __init__(self, rule_id: str, session_id: str, user: str):
        self.rule_id = rule_id
        self.session_id = session_id
        self.user = user


def _fetch_rows(db: SQLSession, scope_filter, since_dt: datetime, until_dt: datetime) -> list[_FlagRow]:
    stmt = (
        select(EventFlag.rule_id, EventFlag.session_id, SessionRow.user)
        .join(SessionRow, EventFlag.session_id == SessionRow.id)
        .where(EventFlag.ts >= since_dt)
        .where(EventFlag.ts < until_dt)
        .where(scope_filter)
    )
    return [_FlagRow(*r) for r in db.exec(stmt).all()]


def _totals(rows: list[_FlagRow]) -> ExceptionAnalyticsTotals:
    return ExceptionAnalyticsTotals(
        flagged_sessions=len({r.session_id for r in rows}),
        total_flags=len(rows),
    )


def _top_rule_ids(rows: list[_FlagRow], limit: int) -> list[ExceptionAnalyticsRuleCount]:
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r.rule_id] += 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [ExceptionAnalyticsRuleCount(rule_id=rid, count=n) for rid, n in ranked[:limit]]


def _top_offenders(rows: list[_FlagRow], limit: int) -> list[ExceptionAnalyticsOffender]:
    per_user: dict[str, list[_FlagRow]] = defaultdict(list)
    for r in rows:
        per_user[r.user].append(r)
    offenders = [
        ExceptionAnalyticsOffender(
            user=user,
            flag_count=len(user_rows),
            flagged_sessions=len({r.session_id for r in user_rows}),
        )
        for user, user_rows in per_user.items()
    ]
    offenders.sort(key=lambda o: o.flag_count, reverse=True)
    return offenders[:limit]


class ExceptionsRouter:
    """GET /analytics/exceptions — exception-first red-flag rollup."""

    def __init__(self) -> None:
        self.router = APIRouter(prefix="/analytics", tags=["receipt:analytics"])
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.get("/exceptions", response_model=ExceptionAnalyticsOut)(self.exceptions)

    def exceptions(
        self,
        request: Request,
        from_: Optional[datetime] = Query(default=None, alias="from"),
        to: Optional[datetime] = Query(default=None),
        org_id: Optional[str] = Query(
            default=None,
            description="Request the org-wide rollup + top-offenders too — "
            "requires org-admin (or, on self-host, any dashboard session).",
        ),
        db: SQLSession = Depends(get_session),
        user_id: UUID = Depends(get_current_user_id),
        token: str = Depends(get_current_user_token),
    ) -> ExceptionAnalyticsOut:
        until_dt = _naive_utc(to) if to is not None else _naive_utc(datetime.now(timezone.utc))
        since_dt = _naive_utc(from_) if from_ is not None else (until_dt - timedelta(days=7))

        # Same caller-email resolution as /analytics/tokens and /dashboard/team.
        _workspace_ids, profile_email = dashboard_router._resolve_scope(user_id)
        caller_email = profile_email
        if not caller_email:
            try:
                caller_email = get_auth_provider().email_from_token(token)
            except Exception:
                caller_email = None

        own_rows = (
            _fetch_rows(db, SessionRow.user == caller_email, since_dt, until_dt)
            if caller_email
            else []
        )
        own = ExceptionAnalyticsScope(
            totals=_totals(own_rows), top_rule_ids=_top_rule_ids(own_rows, _TOP_RULE_IDS_LIMIT)
        )

        org: Optional[ExceptionAnalyticsOrgScope] = None
        if org_id:
            require_org_admin(request, org_id)
            org_rows = _fetch_rows(db, EventFlag.org_id == org_id, since_dt, until_dt)
            org = ExceptionAnalyticsOrgScope(
                totals=_totals(org_rows),
                top_rule_ids=_top_rule_ids(org_rows, _TOP_RULE_IDS_LIMIT),
                top_offenders=_top_offenders(org_rows, _TOP_OFFENDERS_LIMIT),
            )

        return ExceptionAnalyticsOut(
            since=since_dt,
            until=until_dt,
            own=own,
            org=org,
        )
