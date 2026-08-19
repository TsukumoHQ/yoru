"""Receipt — batch sessions export. Contract: vault/BACKEND-API-V1.md §4.6.

Org-walled (M5a, design 44a3774a §3/§6): the same tenant + email/group
visibility as the sessions list (visible_scope_sync). Streams via
StreamingResponse to keep memory bounded; cap 10k sessions per export with
`X-Truncated: true` on overflow.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Iterator, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import or_
from sqlmodel import Session as SQLSession, select

from apps.api.api.services.signing import dsse
from . import eu_ai_act as eu_ai_act_mod
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
        format: Literal["json", "csv", "otlp", "eu-ai-act"] = Query("json"),
        flagged_only: bool = Query(False),
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
        x_organization_id: Optional[str] = Header(None, alias="X-Organization-Id"),
    ) -> Response:
        # M5a (design 44a3774a §3/§6): org-wall the export — the last read path
        # still owner-only. Same tenant wall as the sessions list: a member sees
        # own+group within their org(s); a super-admin (visible None) sees all
        # orgs, or the one named by a valid X-Organization-Id. NULL org_id
        # (legacy/pre-M1 rows) counts as the default org.
        from apps.api.api.services.access.visibility import (
            _DEFAULT_ORG_ID,
            visible_scope_sync,
        )
        visible, orgs = visible_scope_sync(current_user, x_organization_id)
        filters = [] if visible is None else [SessionRow.user.in_(visible)]
        if orgs is not None:
            org_clause = SessionRow.org_id.in_(orgs)
            if _DEFAULT_ORG_ID in orgs:
                org_clause = or_(org_clause, SessionRow.org_id.is_(None))
            filters.append(org_clause)
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

        if format == "eu-ai-act":
            # M5b (design §6 / steal #1): a SIGNED, offline-verifiable compliance
            # bundle. Unlike the streamed formats this is one whole artifact — a
            # DSSE/Ed25519 signature only means something over the complete
            # payload — so it is built in memory (bounded by the 10k cap) and
            # returned as a single JSON download. Requires the deployer's signing
            # key; an unsigned compliance bundle would be worthless, so refuse.
            key = dsse.load_signing_key()
            if key is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "export signing key not configured — set YORU_SIGNING_KEY "
                        "to a base64 Ed25519 seed (generate one with the yoru "
                        "keygen helper). The eu-ai-act bundle must be signed."
                    ),
                )
            projected = [
                eu_ai_act_mod.eu_ai_act_session(
                    r,
                    list(db.exec(
                        select(Event)
                        .where(Event.session_id == r.id)
                        .order_by(Event.id.asc())
                    ).all()),
                )
                for r in rows
            ]
            statement = eu_ai_act_mod.build_statement(
                projected,
                org_scope=orgs,
                truncated=truncated,
                export_cap=_EXPORT_CAP,
                predicate_type=dsse.PREDICATE_TYPE,
            )
            bundle = {**statement, **dsse.build_envelope(statement, key)}
            headers["Content-Disposition"] = (
                f'attachment; filename="yoru-audit-export-{stamp}.eu-ai-act.json"'
            )
            return JSONResponse(bundle, headers=headers)

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
