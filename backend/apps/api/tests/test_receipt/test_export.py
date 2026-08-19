"""Tests for batch sessions export — §4.6 JSONL + CSV shapes.

Covers the task-brief acceptance criteria:
  - JSONL: one session per line, events inline, caller-scoped
  - CSV: exact column order per task spec
  - `flagged_only=true` filter
  - 10k cap + `X-Truncated` header
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.models import Event
from apps.api.api.routers.receipt.models import Session as SessionRow

BASE_TS = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)

_CSV_COLUMNS = [
    "user_email", "started_at", "ended_at", "duration_sec",
    "tools_count", "files_count", "cost_usd",
    "flagged", "flags_csv", "summary",
]


@pytest.fixture()
def app(engine) -> FastAPI:
    """Mount ONLY the ExportRouter so this module runs independently."""
    from apps.api.api.routers.receipt.db import get_session
    from apps.api.api.routers.receipt.export_router import ExportRouter

    _app = FastAPI()
    _app.include_router(ExportRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture()
def alice_headers(mint_token):
    _, h = mint_token("alice")
    return h


def _seed(db_session, sid: str, user: str, *, flagged: bool = False,
          flags: list[str] | None = None, offset_sec: int = 0,
          duration_sec: int = 60) -> None:
    started = BASE_TS + timedelta(seconds=offset_sec)
    db_session.add(SessionRow(
        id=sid, user=user,
        started_at=started,
        ended_at=started + timedelta(seconds=duration_sec),
        tools_count=3, files_count=2,
        cost_usd=0.25,
        flagged=flagged, flags=flags or [],
        files_changed=["a.py"], tools_called=["Bash"],
        summary="test summary",
    ))


def test_export_jsonl_shape_and_user_scope(client, db_session, alice_headers, mint_token):
    """JSONL: one session per line, events inline, caller-only sessions."""
    _seed(db_session, "s1", "alice", offset_sec=0)
    _seed(db_session, "s2", "alice", offset_sec=10, flagged=True, flags=["shell_rm"])
    _seed(db_session, "s3", "bob", offset_sec=5)  # must be excluded
    db_session.add(Event(
        session_id="s2", ts=BASE_TS + timedelta(seconds=11),
        kind="tool_use", tool="Bash", content="rm -rf /", flags=["shell_rm"],
    ))
    db_session.commit()

    resp = client.get(
        "/api/v1/sessions/export?format=json", headers=alice_headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["x-truncated"] == "false"
    assert 'filename="receipt-export-' in resp.headers["content-disposition"]

    lines = [ln for ln in resp.text.split("\n") if ln]
    assert len(lines) == 2  # alice only, bob excluded
    parsed = [json.loads(ln) for ln in lines]
    ids = {p["session_id"] for p in parsed}
    assert ids == {"s1", "s2"}
    for p in parsed:
        assert p["user_email"] == "alice"
        assert p["duration_sec"] == 60.0
        assert isinstance(p["events"], list)
    s2 = next(p for p in parsed if p["session_id"] == "s2")
    assert s2["flagged"] is True
    assert len(s2["events"]) == 1
    assert s2["events"][0]["content"] == "rm -rf /"


def test_export_csv_shape_and_flagged_only(client, db_session, alice_headers):
    """CSV: exact columns in order; flagged_only filter excludes non-flagged rows."""
    _seed(db_session, "c1", "alice", offset_sec=0)
    _seed(
        db_session, "c2", "alice", offset_sec=10,
        flagged=True, flags=["secret_aws", "shell_rm"],
    )
    db_session.commit()

    resp = client.get(
        "/api/v1/sessions/export?format=csv&flagged_only=true",
        headers=alice_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["x-truncated"] == "false"
    assert resp.headers["content-disposition"].endswith('.csv"')

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    assert rows[0] == _CSV_COLUMNS
    assert len(rows) == 2  # header + 1 flagged row
    row = rows[1]
    assert row[0] == "alice"
    assert row[3] == "60"  # duration_sec
    assert row[4] == "3"   # tools_count
    assert row[7] == "true"  # flagged
    assert row[8] == "secret_aws,shell_rm"  # flags_csv
    assert row[9] == "test summary"


def test_export_requires_auth(client):
    assert client.get("/api/v1/sessions/export").status_code == 401


def test_export_walls_cross_org(client, db_session, mint_token):
    """M5a (design 44a3774a §3/§6): export is org-walled like the sessions list.

    A CLI-token identity (no profile) is scoped to the default org. Seed a
    default-org session (NULL org_id counts as default) plus a same-user
    other-org session; only the default-org one may be exported — the
    other-org row is walled out even though the caller owns it.
    """
    _raw, headers = mint_token("u-1")
    _seed(db_session, "e-mine", "u-1", offset_sec=0)          # org_id NULL → default
    _seed(db_session, "e-other", "u-1", offset_sec=10)
    db_session.get(SessionRow, "e-other").org_id = "org-acme"  # other tenant
    db_session.commit()

    resp = client.get("/api/v1/sessions/export?format=json", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {json.loads(ln)["session_id"] for ln in resp.text.split("\n") if ln}
    assert ids == {"e-mine"}  # org-acme session walled out of the export
