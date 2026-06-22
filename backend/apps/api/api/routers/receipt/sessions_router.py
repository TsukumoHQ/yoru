"""Receipt v0 — sessions list + detail router.

Owner: dev-B. Contract: vault/BACKEND-API-V0.md §4.2, §4.3.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlmodel import Session as SQLSession, select

import os

from .db import get_session
from .deps import require_current_user
from dataclasses import asdict
from .models import (
    Event,
    EventOut,
    FileChangedOut,
    ScoreBreakdown,
    Session as SessionRow,
    SessionDetail,
    SessionListItem,
    SessionListResponse,
    ShareIn,
    ShareOut,
    TrailOut,
    TrailSession,
)
from .scoring import compute_score

# Public site that serves /s/<id>. The marketing app (marketing/) owns that
# route — see PublicSessionPage. Override in test/stage via env.
_PUBLIC_SITE_BASE = os.environ.get("YORU_PUBLIC_URL", "https://yoru.sh").rstrip("/")


def _public_session_url(session_id: str) -> str:
    return f"{_PUBLIC_SITE_BASE}/s/{session_id}"


def _session_visible(row, current_user: str) -> bool:
    """Group-scoped access: the owner, their group-mates, or an admin may act on
    a session. The confidentiality wall is between groups, not within one. A
    missing row returns False so callers 404 (not 403) and don't leak existence.
    """
    if row is None:
        return False
    from apps.api.api.services.access.visibility import visible_emails_sync
    visible = visible_emails_sync(current_user)
    return visible is None or row.user in visible

# Tool-name classes for path/content extraction (v1 timeline enrichment).
_FILE_TOOLS = frozenset({"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"})

# Tools that WRITE files (distinct from the read-only _FILE_TOOLS above).
_FILE_WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


_TOOL_INPUT_STR_CAP = 4000
_TOOL_INPUT_LIST_CAP = 50


def _cap_value(v):
    """Recursively cap a tool_input value for wire transport.

    Strings → truncated to _TOOL_INPUT_STR_CAP chars with a trailing marker.
    Lists → first _TOOL_INPUT_LIST_CAP items, recursively capped.
    Dicts → recursively capped.
    Everything else → passed through.
    """
    if isinstance(v, str):
        if len(v) > _TOOL_INPUT_STR_CAP:
            return v[:_TOOL_INPUT_STR_CAP] + f"…[+{len(v) - _TOOL_INPUT_STR_CAP} chars]"
        return v
    if isinstance(v, list):
        capped = [_cap_value(x) for x in v[:_TOOL_INPUT_LIST_CAP]]
        if len(v) > _TOOL_INPUT_LIST_CAP:
            capped.append(f"…[+{len(v) - _TOOL_INPUT_LIST_CAP} items]")
        return capped
    if isinstance(v, dict):
        return {k: _cap_value(vv) for k, vv in v.items()}
    return v


def _cap_tool_input(ti: dict) -> dict:
    return {k: _cap_value(v) for k, v in ti.items()}


def _count_lines(s: str | None) -> int:
    if not s:
        return 0
    # Count lines the way a diff would: non-empty content => at least 1.
    return s.count("\n") + (1 if s and not s.endswith("\n") else 0)


def _summarize_files_changed(events_asc: list[Event]) -> list[FileChangedOut]:
    """Aggregate file-change events into one FileChangedOut per path.

    op: first seen Write => create, otherwise edit. Delete is not yet emitted
    by the hook (v1 future). additions/deletions are line counts pulled from
    tool_input.new_string / old_string (Edit), tool_input.content (Write),
    summed across all events touching the same path.
    """
    # Walk events in REVERSE chrono so the first time we see a path is its
    # MOST RECENT write — and the output list preserves that order (Python
    # dict insertion order), putting the freshest file at the top of the
    # rail panel. Matches the Timeline + Red Flags "newest-first" convention.
    by_path: dict[str, FileChangedOut] = {}
    for e in reversed(events_asc):
        # Include any event that writes a file — either kind=file_change (post
        # inference by events_router) OR a tool_use whose tool is a known
        # writer. Historical rows where the hook posted kind=tool_use without
        # letting _infer_kind fire (early v0 ingests) would otherwise be
        # dropped, leaving an empty Files Changed rail.
        tool_for_check = e.tool or (e.raw.get("tool_name") if isinstance(e.raw, dict) else None)
        is_writer = tool_for_check in _FILE_WRITE_TOOLS
        if not (e.kind == "file_change" or is_writer):
            continue
        path = e.path
        if not path:
            continue
        raw = e.raw if isinstance(e.raw, dict) else {}
        tool_input = raw.get("tool_input") if isinstance(raw.get("tool_input"), dict) else {}
        tool = e.tool or raw.get("tool_name")
        # Compute adds/dels only for known writer tools; other file_change
        # rows (test fixtures, legacy ingests) still surface with zero counts
        # so the UI can show the row.
        adds, dels = 0, 0
        if tool == "Write":
            adds = _count_lines(tool_input.get("content") if isinstance(tool_input, dict) else None)
        elif tool == "Edit":
            adds = _count_lines(tool_input.get("new_string") if isinstance(tool_input, dict) else None)
            dels = _count_lines(tool_input.get("old_string") if isinstance(tool_input, dict) else None)
        elif tool == "MultiEdit":
            edits = tool_input.get("edits") if isinstance(tool_input, dict) else None
            if isinstance(edits, list):
                for ed in edits:
                    if isinstance(ed, dict):
                        adds += _count_lines(ed.get("new_string") if isinstance(ed.get("new_string"), str) else None)
                        dels += _count_lines(ed.get("old_string") if isinstance(ed.get("old_string"), str) else None)
        elif tool == "NotebookEdit":
            adds = _count_lines(tool_input.get("new_source") if isinstance(tool_input, dict) else None)
        existing = by_path.get(path)
        if existing is None:
            op = "create" if tool == "Write" else "edit"
            by_path[path] = FileChangedOut(path=path, op=op, additions=adds, deletions=dels)
        else:
            by_path[path] = FileChangedOut(
                path=path,
                op=existing.op,
                additions=existing.additions + adds,
                deletions=existing.deletions + dels,
            )
    return list(by_path.values())


def _enrich_events(events_asc: list[Event]) -> list[EventOut]:
    """Compute tool/path/content/duration_ms/group_key per event for the frontend
    timeline. Pure serialization-time enrichment — nothing persists to the DB."""
    out: list[EventOut] = []
    n = len(events_asc)
    for i, e in enumerate(events_asc):
        raw = e.raw if isinstance(e.raw, dict) else {}
        tool_input = raw.get("tool_input") if isinstance(raw.get("tool_input"), dict) else {}
        tool = e.tool or (raw.get("tool_name") if isinstance(raw.get("tool_name"), str) else None)
        path = e.path
        if path is None:
            if tool in _FILE_TOOLS and isinstance(tool_input.get("file_path"), str):
                path = tool_input["file_path"]
            elif tool == "Bash" and isinstance(tool_input.get("command"), str):
                path = tool_input["command"][:80]
            elif tool == "Grep" and isinstance(tool_input.get("pattern"), str):
                path = tool_input["pattern"]
            elif tool == "WebSearch" and isinstance(tool_input.get("query"), str):
                path = tool_input["query"]
        content = e.content
        if content is None:
            src = None
            if tool == "Bash":
                src = tool_input.get("command")
            elif tool == "Edit":
                src = tool_input.get("old_string")
            elif tool == "Write":
                src = tool_input.get("content")
            elif tool == "Grep":
                src = tool_input.get("pattern")
            elif tool == "Read":
                src = tool_input.get("file_path")
            elif tool == "WebSearch":
                src = tool_input.get("query")
            elif tool == "WebFetch":
                src = tool_input.get("url")
            elif tool == "Task":
                src = tool_input.get("description")
            elif tool == "TodoWrite":
                todos = tool_input.get("todos")
                if isinstance(todos, list) and todos:
                    first = todos[0]
                    if isinstance(first, dict):
                        src = first.get("content") or first.get("activeForm")
            if isinstance(src, str):
                content = src[:200]
            elif content is None and isinstance(tool, str) and tool_input:
                # Generic fallback for MCP tools and anything else:
                # pick the first non-empty string value from tool_input, prefixed by its key.
                try:
                    for k, v in tool_input.items():
                        if isinstance(v, str) and v.strip():
                            content = f"{k}={v}"[:200]
                            break
                        elif isinstance(v, (int, float, bool)) and v is not None:
                            content = f"{k}={v}"[:200]
                            break
                        elif isinstance(v, (list, dict)) and v:
                            import json as _json
                            content = f"{k}={_json.dumps(v)}"[:200]
                            break
                except Exception:
                    pass
        duration_ms: Optional[int] = None
        if i < n - 1:
            duration_ms = int(round((events_asc[i + 1].ts - e.ts).total_seconds() * 1000))
        group_key = f"{tool}:{(path or content or '')[:40]}"
        # Extract tool_response preview for timeline. Shapes by tool type:
        # - Bash: dict{stdout,stderr,error}
        # - Read: dict{content}
        # - Edit/Write: dict{message|content}
        # - MCP tools (mcp__*): list[{type:"text", text:"..."}]
        # - Sometimes plain string.
        output: Optional[str] = None
        tr = raw.get("tool_response")
        if isinstance(tr, dict):
            parts = []
            if isinstance(tr.get("stdout"), str) and tr["stdout"]:
                parts.append(tr["stdout"])
            if isinstance(tr.get("stderr"), str) and tr["stderr"]:
                parts.append(tr["stderr"])
            if isinstance(tr.get("error"), str) and tr["error"]:
                parts.append(f"error: {tr['error']}")
            if isinstance(tr.get("content"), str) and tr["content"]:
                parts.append(tr["content"])
            if isinstance(tr.get("message"), str) and tr["message"]:
                parts.append(tr["message"])
            blob = "\n".join(parts).strip()
            if blob:
                output = blob[:800]
        elif isinstance(tr, list) and tr:
            # MCP shape: list of {type:"text", text:"..."} items.
            parts = []
            for item in tr:
                if isinstance(item, dict) and item.get("type") == "text":
                    t = item.get("text")
                    if isinstance(t, str):
                        parts.append(t)
                elif isinstance(item, str):
                    parts.append(item)
            blob = "\n".join(parts).strip()
            if blob:
                output = blob[:800]
        elif isinstance(tr, str) and tr.strip():
            output = tr[:800]
        # Structured tool_input (capped) for the frontend per-tool detail view.
        # Strings inside get capped at 4000 chars each, lists at 50 items.
        # Non-dict raw.tool_input values are dropped — the frontend expects
        # dict-shaped input for its renderers.
        tool_input_out: Optional[dict] = None
        if isinstance(raw.get("tool_input"), dict):
            tool_input_out = _cap_tool_input(raw["tool_input"])

        out.append(EventOut.model_validate({
            **e.model_dump(),
            "tool": tool,
            "path": path,
            "content": content,
            "duration_ms": duration_ms,
            "group_key": group_key,
            "output": output,
            "tool_input": tool_input_out,
        }))
        # Synthetic error event: when tool_response carries an error, emit a
        # second EventOut (kind="error") sharing the same ts so the frontend
        # renders [err] the agent's failure adjacent to the [tool] call.
        # Not persisted — virtual row, id reuses the tool event's id (negated
        # to avoid clashing with real event ids on the client).
        err_text: Optional[str] = None
        if isinstance(tr, dict) and isinstance(tr.get("error"), str) and tr["error"].strip():
            err_text = tr["error"].strip()
        if err_text:
            out.append(EventOut.model_validate({
                **e.model_dump(),
                "id": -abs(e.id) if e.id else 0,
                "kind": "error",
                "tool": tool,
                "path": path,
                "content": err_text[:400],
                "duration_ms": 0,
                "group_key": f"err:{(path or '')[:40]}",
                "output": None,
            }))
    return out


class SessionsRouter:
    """Read endpoints for Receipt sessions (list + detail)."""

    def __init__(self) -> None:
        self.router = APIRouter(prefix="/sessions", tags=["receipt:sessions"])
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.get("", response_model=SessionListResponse)(self.list_sessions)
        self.router.get("/{session_id}", response_model=SessionDetail)(
            self.get_session_detail
        )
        self.router.get("/{session_id}/trail", response_model=TrailOut)(
            self.get_session_trail
        )
        self.router.get("/{session_id}/verify")(self.verify_session)
        self.router.delete("/{session_id}/tailer-events", status_code=204)(
            self.delete_tailer_events
        )
        # Issue #79 — opt-in public share. Both endpoints are authed +
        # owner-only. Idempotent (share sets to true, revoke sets to false).
        self.router.post("/{session_id}/share", response_model=ShareOut)(
            self.share_session
        )
        self.router.post(
            "/{session_id}/share/revoke", response_model=ShareOut
        )(self.revoke_share)

    def list_sessions(
        self,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
        flagged: Optional[bool] = None,
        min_cost: Optional[float] = None,
        workspace_id: Optional[str] = Query(None),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> SessionListResponse:
        # Group-scoped visibility: own + group-mates' sessions; admin sees all.
        from apps.api.api.services.access.visibility import visible_emails_sync
        visible = visible_emails_sync(current_user)
        filters = [] if visible is None else [SessionRow.user.in_(visible)]
        if from_ts is not None:
            filters.append(SessionRow.started_at >= from_ts)
        if to_ts is not None:
            filters.append(SessionRow.started_at <= to_ts)
        if flagged is not None:
            filters.append(SessionRow.flagged == flagged)
        if min_cost is not None:
            filters.append(SessionRow.cost_usd >= min_cost)
        if workspace_id is not None:
            filters.append(SessionRow.workspace_id == workspace_id)

        list_stmt = (
            select(SessionRow)
            .where(*filters)
            .order_by(SessionRow.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        count_stmt = select(func.count()).select_from(SessionRow).where(*filters)

        rows = db.exec(list_stmt).all()
        total = db.exec(count_stmt).one()

        items = [SessionListItem.model_validate(r.model_dump()) for r in rows]
        return SessionListResponse(
            items=items, total=total, limit=limit, offset=offset
        )

    def get_session_detail(
        self,
        session_id: str,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> SessionDetail:
        """Return full session detail with events.

        Events are capped at the **last 1000** (most recent by ts) and
        returned in ts ASC order, per BACKEND-API-V0.md §4.3.
        """
        row = db.exec(
            select(SessionRow).where(SessionRow.id == session_id)
        ).first()
        # 404 (not 403) on cross-user to avoid leaking existence.
        if not _session_visible(row, current_user):
            raise HTTPException(status_code=404, detail="session not found")

        # Fetch last 1000 events (ordered DESC, then reverse to ASC) +
        # always include ANY flagged event outside that window. Flagged
        # events are audit-critical and must never be silently dropped.
        recent = db.exec(
            select(Event)
            .where(Event.session_id == session_id)
            .order_by(Event.ts.desc())
            .limit(1000)
        ).all()
        recent_ids = {e.id for e in recent}
        # Pull flagged events older than the window.
        flagged_extra = db.exec(
            select(Event)
            .where(Event.session_id == session_id)
            .where(Event.flags != [])  # type: ignore[arg-type]
        ).all()
        for ev in flagged_extra:
            if ev.id not in recent_ids and (ev.flags or []):
                recent.append(ev)
                recent_ids.add(ev.id)
        events_asc = sorted(recent, key=lambda e: e.ts)

        events_out = _enrich_events(events_asc)
        files_out = _summarize_files_changed(events_asc)

        # Compute score from the event stream + row aggregates. Derives
        # tool_call_count + error_count from the events; the rest from
        # the denormalized row fields. Cheap (O(events)).
        tool_call_count = sum(
            1 for e in events_asc if e.kind in ("tool_use", "file_change")
        )
        error_count = sum(1 for e in events_asc if e.kind == "error")
        score = compute_score(
            files_count=row.files_count,
            tools_called=row.tools_called,
            tokens_output=row.tokens_output,
            tool_call_count=tool_call_count,
            error_count=error_count,
            flags=row.flags,
        )
        score_out = ScoreBreakdown(**asdict(score))

        # SessionDetail.files_changed is now list[FileChangedOut] — overwrite
        # the flat list[str] from row.model_dump() with the structured form.
        # title is already on row.model_dump() (persisted on session row).
        return SessionDetail.model_validate(
            {
                **row.model_dump(),
                "files_changed": files_out,
                "events": events_out,
                "score": score_out,
            }
        )

    def delete_tailer_events(
        self,
        session_id: str,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> Response:
        """Wipe events originating from the transcript tailer for a session.

        Called before a `--backfill` run so re-ingesting the same JSONL doesn't
        double-count tokens / assistant messages / thinking blocks. Identified
        by `raw.hook_event_name == "TranscriptTail"` (the tailer's marker) —
        hook-sourced events (user prompts, tool calls, notifications) are
        left untouched.

        Also rolls back the session aggregate (tokens + cost) by the sum of
        the deleted rows so the totals stay consistent after the wipe. The
        subsequent backfill re-increments them from scratch.
        """
        row = db.exec(
            select(SessionRow).where(SessionRow.id == session_id)
        ).first()
        if not _session_visible(row, current_user):
            raise HTTPException(status_code=404, detail="session not found")

        # Pull the tailer rows first so we can offset the aggregate.
        tailer_rows = db.exec(
            select(Event).where(Event.session_id == session_id)
        ).all()
        offset_in = 0
        offset_out = 0
        offset_cost = 0.0
        deleted = 0
        for ev in tailer_rows:
            raw = ev.raw if isinstance(ev.raw, dict) else {}
            if raw.get("hook_event_name") != "TranscriptTail":
                continue
            offset_in += int(ev.tokens_input or 0)
            offset_out += int(ev.tokens_output or 0)
            offset_cost += float(ev.cost_usd or 0.0)
            db.delete(ev)
            deleted += 1
        if deleted:
            row.tokens_input = max(0, row.tokens_input - offset_in)
            row.tokens_output = max(0, row.tokens_output - offset_out)
            row.cost_usd = max(0.0, row.cost_usd - offset_cost)
            db.add(row)
        db.commit()
        return Response(status_code=204)

    def get_session_trail(
        self,
        session_id: str,
        response: Response,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> TrailOut:
        """Compliance-audit export — full session + ALL events, no cap.

        Per BACKEND-API-V0.md §4.6. Sets Content-Disposition so `curl -OJ`
        saves as `receipt-{session_id}.json`.
        """
        row = db.exec(
            select(SessionRow).where(SessionRow.id == session_id)
        ).first()
        if not _session_visible(row, current_user):
            raise HTTPException(status_code=404, detail="session not found")

        events = db.exec(
            select(Event)
            .where(Event.session_id == session_id)
            .order_by(Event.ts.asc())
        ).all()

        response.headers["Content-Disposition"] = (
            f'attachment; filename="receipt-{session_id}.json"'
        )
        return TrailOut(
            session=TrailSession.model_validate(row.model_dump()),
            events=_enrich_events(list(events)),
            exported_at=datetime.now(timezone.utc),
            schema_version="v0",
        )

    def verify_session(
        self,
        session_id: str,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> dict:
        """Tamper-evidence: recompute the per-session hash chain and report
        whether the audit trail is intact.

        Returns ``intact: true`` only if every event's stored hash matches a
        fresh recomputation AND the chain links are unbroken — so any post-hoc
        edit, deletion, or reorder of events is detectable. This is what makes
        the trail usable as evidence.
        """
        from .events_router import event_entry_hash

        row = db.exec(
            select(SessionRow).where(SessionRow.id == session_id)
        ).first()
        if not _session_visible(row, current_user):
            raise HTTPException(status_code=404, detail="session not found")

        events = db.exec(
            select(Event)
            .where(Event.session_id == session_id)
            .order_by(Event.id.asc())
        ).all()

        prev = ""
        verified = 0
        unchained = 0
        broken_at = None
        for ev in events:
            if ev.entry_hash is None:
                # Pre-chain legacy event (ingested before tamper-evidence) —
                # can't verify, but don't claim tampering.
                unchained += 1
                prev = ev.entry_hash or prev
                continue
            expected = event_entry_hash(
                prev, ev.ts, ev.kind, ev.tool, ev.path, ev.content,
                ev.tokens_input, ev.tokens_output, ev.cost_usd,
            )
            if expected != ev.entry_hash or (ev.prev_hash or "") != prev:
                broken_at = ev.id
                break
            verified += 1
            prev = ev.entry_hash

        intact = broken_at is None
        return {
            "session_id": session_id,
            "intact": intact,
            "event_count": len(events),
            "verified": verified,
            "unchained_legacy": unchained,
            "broken_at_event_id": broken_at,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    # ---- Issue #79 — public share toggle (authed, owner-only, idempotent) ----

    def share_session(
        self,
        session_id: str,
        body: Optional[ShareIn] = None,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> ShareOut:
        """Flip this session public. Idempotent — re-POSTing returns the
        same canonical URL. Only the owner can flip it. 404 (not 403) on
        cross-user to avoid leaking existence, same as /sessions/{id}.

        `body.source` ("dashboard" | "cli") is recorded in app logs so we
        can measure which affordance actually drives share adoption.
        """
        row = db.exec(
            select(SessionRow).where(SessionRow.id == session_id)
        ).first()
        if not _session_visible(row, current_user):
            raise HTTPException(status_code=404, detail="session not found")

        source = (body.source if body is not None else "dashboard")
        # Breadcrumb for analytics. Structured logging middleware picks it up.
        # We don't persist a per-share counter row in v0 — the log aggregator
        # is enough for the "dashboard vs cli" split question.
        print(
            f"[share] session={session_id} user={current_user} "
            f"source={source} transition={'private->public' if not row.is_public else 'noop'}"
        )

        if not row.is_public:
            row.is_public = True
            db.add(row)
            db.commit()

        return ShareOut(
            session_id=session_id,
            is_public=True,
            public_url=_public_session_url(session_id),
        )

    def revoke_share(
        self,
        session_id: str,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> ShareOut:
        """Flip this session back to private. Idempotent."""
        row = db.exec(
            select(SessionRow).where(SessionRow.id == session_id)
        ).first()
        if not _session_visible(row, current_user):
            raise HTTPException(status_code=404, detail="session not found")

        if row.is_public:
            print(
                f"[share] session={session_id} user={current_user} "
                f"transition=public->private"
            )
            row.is_public = False
            db.add(row)
            db.commit()

        return ShareOut(
            session_id=session_id,
            is_public=False,
            public_url=None,
        )
