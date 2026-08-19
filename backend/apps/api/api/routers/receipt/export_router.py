"""Receipt — batch sessions export. Contract: vault/BACKEND-API-V1.md §4.6.

User-scoped only (Session.org_id lands with the v1 backfill). Streams via
StreamingResponse to keep memory bounded; cap 10k sessions per export with
`X-Truncated: true` on overflow.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Iterator, Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlmodel import Session as SQLSession, select

from .db import get_session
from .deps import require_current_user
from .models import Event, Session as SessionRow

_EXPORT_CAP = 10_000

_CSV_COLUMNS = [
    "user_email", "started_at", "ended_at", "duration_sec",
    "tools_count", "files_count", "cost_usd",
    "flagged", "flags_csv", "summary",
]


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.isoformat()


def _duration_sec(row: SessionRow) -> Optional[float]:
    if row.ended_at is None:
        return None
    return (row.ended_at - row.started_at).total_seconds()


def _csv_row(row: SessionRow) -> list[str]:
    dur = _duration_sec(row)
    return [
        row.user,
        _iso(row.started_at) or "",
        _iso(row.ended_at) or "",
        "" if dur is None else f"{dur:.0f}",
        str(row.tools_count),
        str(row.files_count),
        f"{row.cost_usd:.6f}",
        "true" if row.flagged else "false",
        ",".join(row.flags or []),
        row.summary or "",
    ]


def _jsonl_line(row: SessionRow, events: list[Event]) -> str:
    payload = {
        "session_id": row.id,
        "user_email": row.user,
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
        "duration_sec": _duration_sec(row),
        "tools_count": row.tools_count,
        "files_count": row.files_count,
        "cost_usd": row.cost_usd,
        "flagged": row.flagged,
        "flags": row.flags or [],
        "files_changed": row.files_changed or [],
        "tools_called": row.tools_called or [],
        "summary": row.summary,
        "events": [
            {"ts": _iso(e.ts), "kind": e.kind, "tool": e.tool,
             "path": e.path, "content": e.content, "flags": e.flags or []}
            for e in events
        ],
    }
    return json.dumps(payload, default=str) + "\n"


class ExportRouter:
    def __init__(self) -> None:
        self.router = APIRouter(prefix="/sessions", tags=["receipt:export"])
        self.router.get("/export")(self.export_sessions)

    def get_router(self) -> APIRouter:
        return self.router

    def export_sessions(
        self,
        from_: Optional[datetime] = Query(None, alias="from"),
        to: Optional[datetime] = Query(None),
        format: Literal["json", "csv", "otlp"] = Query("json"),
        flagged_only: bool = Query(False),
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> StreamingResponse:
        filters = [SessionRow.user == current_user]
        if from_ is not None:
            filters.append(SessionRow.started_at >= from_)
        if to is not None:
            filters.append(SessionRow.started_at <= to)
        if flagged_only:
            filters.append(SessionRow.flagged == True)  # noqa: E712

        # +1 to detect overflow without a second COUNT query.
        stmt = (
            select(SessionRow)
            .where(*filters)
            .order_by(SessionRow.started_at.asc())
            .limit(_EXPORT_CAP + 1)
        )
        rows = list(db.exec(stmt).all())
        truncated = len(rows) > _EXPORT_CAP
        if truncated:
            rows = rows[:_EXPORT_CAP]

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        headers = {"X-Truncated": "true" if truncated else "false"}

        if format == "csv":
            def gen_csv() -> Iterator[str]:
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow(_CSV_COLUMNS)
                yield buf.getvalue()
                buf.seek(0); buf.truncate(0)
                for r in rows:
                    writer.writerow(_csv_row(r))
                    yield buf.getvalue()
                    buf.seek(0); buf.truncate(0)
            headers["Content-Disposition"] = (
                f'attachment; filename="receipt-export-{stamp}.csv"'
            )
            return StreamingResponse(gen_csv(), media_type="text/csv", headers=headers)

        if format == "otlp":
            # OTel-GenAI interop export (design §1/§8): one OTLP/JSON trace
            # (ExportTraceServiceRequest) per session, one per line — JSONL of
            # traces keeps the streaming + 10k-cap contract instead of buffering
            # one giant array. Owner-scoped, full fidelity (uncapped raw payload
            # + chain overlay). Any OTel backend can ingest each line as-is.
            from . import otel

            def gen_otlp() -> Iterator[str]:
                for r in rows:
                    events = list(db.exec(
                        select(Event)
                        .where(Event.session_id == r.id)
                        .order_by(Event.id.asc())
                    ).all())
                    yield json.dumps(otel.trace_from_rows(r, events), default=str) + "\n"
            headers["Content-Disposition"] = (
                f'attachment; filename="receipt-export-{stamp}.otlp.jsonl"'
            )
            return StreamingResponse(
                gen_otlp(), media_type="application/x-ndjson", headers=headers
            )

        def gen_jsonl() -> Iterator[str]:
            for r in rows:
                events = list(db.exec(
                    select(Event)
                    .where(Event.session_id == r.id)
                    .order_by(Event.ts.asc())
                ).all())
                yield _jsonl_line(r, events)
        headers["Content-Disposition"] = (
            f'attachment; filename="receipt-export-{stamp}.jsonl"'
        )
        return StreamingResponse(
            gen_jsonl(), media_type="application/x-ndjson", headers=headers
        )
