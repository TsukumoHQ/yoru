"""Tests for GET /api/v1/dashboard/team (team dashboard aggregates)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.dashboard_router import DashboardRouter
from apps.api.api.routers.receipt.db import get_session
from apps.api.api.routers.receipt.models import Session as SessionRow


_CALLER_EMAIL = "alice@x.io"
_SCOPE_WS = "ws-test"


@pytest.fixture()
def app(engine) -> FastAPI:
    """Mount only the DashboardRouter — isolated from sibling routers."""
    _app = FastAPI()
    _app.include_router(DashboardRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture(autouse=True)
def _patch_scope(monkeypatch):
    """`_resolve_scope` reads org/workspace membership from the Supabase client
    (`.table(...)`), which a local self-hosted store can't answer — so it would
    return ([], None) and the dashboard would aggregate nothing. Pin a
    deterministic scope so the tests exercise the real SQL aggregation: the
    caller is `alice@x.io` and sees workspace `ws-test`.
    (Self-host gap flagged separately: `_resolve_scope` is Supabase-coupled.)"""
    import apps.api.api.routers.receipt.dashboard_router as dr

    monkeypatch.setattr(dr, "_resolve_scope", lambda _uid: ([_SCOPE_WS], _CALLER_EMAIL))


@pytest.fixture()
def client(app, session_cookie_for) -> TestClient:
    c = TestClient(app)
    from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME

    c.cookies.set(SESSION_COOKIE_NAME, session_cookie_for(_CALLER_EMAIL))
    return c


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed(db: SQLSession, *rows: SessionRow) -> None:
    for r in rows:
        db.add(r)
    db.commit()


def test_empty_db_returns_zeros(client: TestClient) -> None:
    resp = client.get("/api/v1/dashboard/team")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "users": [],
        "totals": {"sessions": 0, "flagged": 0, "flagged_pct": 0.0},
    }


def test_self_host_scope_falls_back_to_jwt_email(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    """Self-host: with no Supabase, _resolve_scope returns ([], None). The
    caller must still see THEIR OWN sessions, scoped by the email in the verified
    JWT — not an empty caller_email that matches nothing. (TSU-63 fix #1.)"""
    import apps.api.api.routers.receipt.dashboard_router as dr

    monkeypatch.setattr(dr, "_resolve_scope", lambda _uid: ([], None))
    now = _now()
    _seed(
        db_session,
        SessionRow(
            id="mine", user=_CALLER_EMAIL, started_at=now,
            cost_usd=1.0, flagged=True,
        ),
        SessionRow(
            id="other", user="someone-else@x.io", started_at=now,
            cost_usd=2.0, flagged=False,
        ),
    )
    resp = client.get("/api/v1/dashboard/team")
    assert resp.status_code == 200
    body = resp.json()
    assert {u["email"] for u in body["users"]} == {_CALLER_EMAIL}
    assert body["totals"]["sessions"] == 1
    assert body["totals"]["flagged"] == 1


def test_populated_db_returns_groupings(
    client: TestClient, db_session: SQLSession
) -> None:
    now = _now()
    _seed(
        db_session,
        SessionRow(
            id="s1", user="alice@x.io", started_at=now,
            cost_usd=1.50, flagged=True, workspace_id=_SCOPE_WS,
        ),
        SessionRow(
            id="s2", user="alice@x.io", started_at=now,
            cost_usd=0.75, flagged=False, workspace_id=_SCOPE_WS,
        ),
        SessionRow(
            id="s3", user="bob@x.io", started_at=now,
            cost_usd=2.25, flagged=False, workspace_id=_SCOPE_WS,
        ),
    )

    resp = client.get("/api/v1/dashboard/team")
    assert resp.status_code == 200
    body = resp.json()

    by_email = {u["email"]: u for u in body["users"]}
    assert by_email["alice@x.io"] == {
        "email": "alice@x.io",
        "sessions": 2,
        "flagged": 1,
        "total_cost_usd": 2.25,
    }
    assert by_email["bob@x.io"] == {
        "email": "bob@x.io",
        "sessions": 1,
        "flagged": 0,
        "total_cost_usd": 2.25,
    }

    assert body["totals"]["sessions"] == 3
    assert body["totals"]["flagged"] == 1
    assert body["totals"]["flagged_pct"] == pytest.approx(1 / 3)


def test_since_filter_drops_older_rows(
    client: TestClient, db_session: SQLSession
) -> None:
    now = _now()
    old = now - timedelta(days=30)
    _seed(
        db_session,
        SessionRow(
            id="new", user="alice@x.io", started_at=now,
            cost_usd=1.00, flagged=True, workspace_id=_SCOPE_WS,
        ),
        SessionRow(
            id="old", user="alice@x.io", started_at=old,
            cost_usd=9.99, flagged=True, workspace_id=_SCOPE_WS,
        ),
    )

    cutoff = (now - timedelta(days=1)).isoformat() + "Z"
    resp = client.get(f"/api/v1/dashboard/team?since={cutoff}")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["users"]) == 1
    assert body["users"][0] == {
        "email": "alice@x.io",
        "sessions": 1,
        "flagged": 1,
        "total_cost_usd": 1.00,
    }
    assert body["totals"] == {
        "sessions": 1,
        "flagged": 1,
        "flagged_pct": 1.0,
    }


def test_future_since_returns_empty(
    client: TestClient, db_session: SQLSession
) -> None:
    _seed(
        db_session,
        SessionRow(
            id="s1", user="alice@x.io", started_at=_now(),
            cost_usd=1.00, flagged=False, workspace_id=_SCOPE_WS,
        ),
    )
    resp = client.get("/api/v1/dashboard/team?since=2099-01-01T00:00:00Z")
    assert resp.status_code == 200
    assert resp.json() == {
        "users": [],
        "totals": {"sessions": 0, "flagged": 0, "flagged_pct": 0.0},
    }
