"""Activity feed — a cross-session, group-scoped, curated event stream.

What an agent DID, action by action, newest first: tool calls, file edits, and
errors across the sessions the caller is entitled to see. NOT a session list.

Confidentiality reuses the same wall as the sessions list: events are joined to
their owning session and filtered by ``visible_emails_sync`` (own + group-mates;
admin sees all; cross-group wall). Authed dashboard only — never the public
``/s/:id`` surface.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlmodel import Session as SQLSession, select

from .db import get_session
from .deps import require_current_user
from .models import (
    ActivityItem,
    ActivityResponse,
    Event,
    Session as SessionRow,
)

# Curated kinds — what an agent DID. Red flags ride on tool/file events, so
# these already capture the flagged ones; token / message / session_start /
# session_end noise is skipped.
_ACTIVITY_KINDS = ("tool_use", "file_change", "error")


class ActivityRouter:
    def __init__(self) -> None:
        self.router = APIRouter(prefix="/activity", tags=["receipt:activity"])

    def get_router(self) -> APIRouter:
        self.router.get("", response_model=ActivityResponse)(self.list_activity)
        return self.router

    def list_activity(
        self,
        limit: int = Query(30, ge=1, le=200),
        offset: int = Query(0, ge=0),
        flagged: Optional[bool] = Query(default=None),
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> ActivityResponse:
        # Group-scoped: events only from sessions the caller may see. Reuses the
        # exact resolver the sessions list uses, so the feed and the list agree.
        from apps.api.api.services.access.visibility import visible_emails_sync

        visible = visible_emails_sync(current_user)
        filters = [Event.kind.in_(_ACTIVITY_KINDS)]
        if visible is not None:
            filters.append(SessionRow.user.in_(visible))
        # Exception-first feed (78766abf): same "is this event flagged"
        # predicate as the session detail/trail views (`bool(e.flags or [])`)
        # — Event.flags is the per-event source, not the session-level
        # SessionRow.flagged column /sessions filters on. Applied AFTER the
        # visibility filter above, so this never widens what's visible.
        # flagged=False/omitted is a no-op (unfiltered), matching /sessions'
        # backward-compatible default.
        if flagged:
            filters.append(func.json_array_length(Event.flags) > 0)

        stmt = (
            select(Event, SessionRow.user, SessionRow.agent)
            .join(SessionRow, Event.session_id == SessionRow.id)
            .where(*filters)
            .order_by(Event.ts.desc(), Event.id.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = db.exec(stmt).all()

        items = [
            ActivityItem(
                id=e.id,
                session_id=e.session_id,
                ts=e.ts,
                user=user,
                agent=agent,
                kind=e.kind,
                tool=e.tool,
                path=e.path,
                flags=list(e.flags or []),
            )
            for (e, user, agent) in rows
        ]
        return ActivityResponse(items=items, limit=limit, offset=offset)
