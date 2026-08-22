"""Tests for GET /api/v1/analytics/tokens (9be89019).

RBAC contract under test (cto-tsukumo, interim guard ahead of d2cf7c71):
  - `own` (caller's own usage) is ALWAYS returned, no gate.
  - `org` is returned ONLY when `org_id` is passed AND the caller clears
    `require_org_admin` — a dev must never pull a colleague's usage.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.analytics_router import AnalyticsRouter
from apps.api.api.routers.receipt.db import get_session
from apps.api.api.routers.receipt.models import Session as SessionRow

_CALLER_EMAIL = "alice@x.io"
_OTHER_EMAIL = "bob@x.io"
_ORG_ID = "org-test"


@pytest.fixture()
def app(engine) -> FastAPI:
    """Mount only the AnalyticsRouter — isolated from sibling routers."""
    _app = FastAPI()
    _app.include_router(AnalyticsRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture(autouse=True)
def _patch_scope(monkeypatch):
    """Same rationale as test_dashboard.py: `_resolve_scope` is Supabase-coupled
    and would return ([], None) against a local store. Pin a deterministic
    caller email so the caller-email resolution path is exercised without a
    live Supabase call."""
    import apps.api.api.routers.receipt.dashboard_router as dr

    monkeypatch.setattr(dr, "_resolve_scope", lambda _uid: ([], _CALLER_EMAIL))


@pytest.fixture()
def client(app, session_cookie_for) -> TestClient:
    c = TestClient(app)
    from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME

    c.cookies.set(SESSION_COOKIE_NAME, session_cookie_for(_CALLER_EMAIL))
    return c


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _seed(db: SQLSession, *rows: SessionRow) -> None:
    for r in rows:
        db.add(r)
    db.commit()


def test_own_scope_always_returned_no_gate(client: TestClient, db_session: SQLSession) -> None:
    now = _now()
    _seed(
        db_session,
        SessionRow(
            id="s1", user=_CALLER_EMAIL, started_at=now,
            cost_usd=1.5, tokens_input=1000, tokens_output=200,
        ),
        SessionRow(
            id="s2", user=_OTHER_EMAIL, started_at=now,
            cost_usd=9.0, tokens_input=5000, tokens_output=900,
        ),
    )
    resp = client.get("/api/v1/analytics/tokens")
    assert resp.status_code == 200
    body = resp.json()
    assert body["own"]["totals"] == {
        "tokens_input": 1000, "tokens_output": 200, "cost_usd": 1.5,
    }
    assert len(body["own"]["series"]) == 1
    assert body["org"] is None


def test_org_scope_omitted_without_org_id(client: TestClient, db_session: SQLSession) -> None:
    _seed(
        db_session,
        SessionRow(id="s1", user=_CALLER_EMAIL, started_at=_now(), cost_usd=1.0),
    )
    resp = client.get("/api/v1/analytics/tokens")
    assert resp.status_code == 200
    assert resp.json()["org"] is None


def test_org_scope_requires_auth(app: FastAPI, db_session: SQLSession) -> None:
    """No dashboard session at all: own-scope path 401s (get_current_user_id
    requires auth), confirming this endpoint isn't reachable unauthenticated."""
    anon = TestClient(app)
    resp = anon.get(f"/api/v1/analytics/tokens?org_id={_ORG_ID}")
    assert resp.status_code == 401


def test_org_scope_gated_by_require_org_admin(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    """Caller IS authenticated (own-scope works) but is NOT an org-admin for
    the requested org_id — the org-wide surface must reject, not silently
    return data. This is the load-bearing "no inter-dev leak" assertion."""
    from fastapi import HTTPException

    import apps.api.api.routers.receipt.analytics_router as ar

    def _deny(request, org_id):
        raise HTTPException(status_code=403, detail="Owner or admin role required for this action")

    monkeypatch.setattr(ar, "require_org_admin", _deny)

    _seed(
        db_session,
        SessionRow(id="s1", user=_CALLER_EMAIL, started_at=_now(), cost_usd=1.0),
        SessionRow(id="s2", user=_OTHER_EMAIL, started_at=_now(), cost_usd=9.0, org_id=_ORG_ID),
    )
    resp = client.get(f"/api/v1/analytics/tokens?org_id={_ORG_ID}")
    assert resp.status_code == 403


def test_org_scope_returned_when_admin_authorized(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    """Caller clears require_org_admin (self-host: any authenticated dashboard
    user) — org-wide totals/series/top_spenders are returned, scoped to
    org_id, not bleeding in rows from other orgs."""
    import apps.api.api.routers.receipt.analytics_router as ar

    monkeypatch.setattr(ar, "require_org_admin", lambda request, org_id: _CALLER_EMAIL)

    now = _now()
    _seed(
        db_session,
        SessionRow(
            id="s1", user=_CALLER_EMAIL, started_at=now, org_id=_ORG_ID,
            cost_usd=1.0, tokens_input=100, tokens_output=10,
        ),
        SessionRow(
            id="s2", user=_OTHER_EMAIL, started_at=now, org_id=_ORG_ID,
            cost_usd=4.0, tokens_input=400, tokens_output=40,
        ),
        SessionRow(
            id="s3", user="carol@other-org.io", started_at=now, org_id="org-other",
            cost_usd=99.0, tokens_input=99999, tokens_output=9999,
        ),
    )
    resp = client.get(f"/api/v1/analytics/tokens?org_id={_ORG_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["org"] is not None
    assert body["org"]["totals"] == {
        "tokens_input": 500, "tokens_output": 50, "cost_usd": 5.0,
    }
    spenders = {s["user"]: s for s in body["org"]["top_spenders"]}
    assert set(spenders) == {_CALLER_EMAIL, _OTHER_EMAIL}
    assert spenders[_OTHER_EMAIL]["cost_usd"] == 4.0
    assert spenders[_CALLER_EMAIL]["cost_usd"] == 1.0
    # top_spenders sorted desc by cost_usd
    assert body["org"]["top_spenders"][0]["user"] == _OTHER_EMAIL


def test_bucket_week_groups_across_days(client: TestClient, db_session: SQLSession) -> None:
    monday = _now().replace(hour=12, minute=0, second=0, microsecond=0)
    monday = monday - timedelta(days=monday.weekday())
    tuesday = monday + timedelta(days=1)
    _seed(
        db_session,
        SessionRow(id="s1", user=_CALLER_EMAIL, started_at=monday, cost_usd=1.0, tokens_input=10, tokens_output=1),
        SessionRow(id="s2", user=_CALLER_EMAIL, started_at=tuesday, cost_usd=2.0, tokens_input=20, tokens_output=2),
    )
    resp = client.get(
        "/api/v1/analytics/tokens",
        params={"bucket": "week", "from": (monday - timedelta(days=1)).isoformat() + "Z"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bucket"] == "week"
    assert len(body["own"]["series"]) == 1
    assert body["own"]["series"][0]["tokens_input"] == 30
    assert body["own"]["totals"]["cost_usd"] == 3.0


def test_since_until_range_filters_rows(client: TestClient, db_session: SQLSession) -> None:
    now = _now()
    old = now - timedelta(days=30)
    _seed(
        db_session,
        SessionRow(id="new", user=_CALLER_EMAIL, started_at=now, cost_usd=1.0),
        SessionRow(id="old", user=_CALLER_EMAIL, started_at=old, cost_usd=9.99),
    )
    cutoff = (now - timedelta(days=1)).isoformat() + "Z"
    resp = client.get(f"/api/v1/analytics/tokens?from={cutoff}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["own"]["totals"]["cost_usd"] == 1.0


def test_empty_db_returns_zeroed_own_scope(client: TestClient, db_session: SQLSession) -> None:
    resp = client.get("/api/v1/analytics/tokens")
    assert resp.status_code == 200
    body = resp.json()
    assert body["own"]["totals"] == {"tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0}
    assert body["own"]["series"] == []
    assert body["org"] is None


# ---------- by_dev / by_project drill dimensions (8c5d82ad) ----------


def test_org_scope_by_dev_returns_full_roster_not_just_top_n(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    """by_dev must include every dev with usage in range, unlike top_spenders
    which stays capped — this is the whole point of the new field."""
    import apps.api.api.routers.receipt.analytics_router as ar

    monkeypatch.setattr(ar, "_TOP_SPENDERS_LIMIT", 1)
    monkeypatch.setattr(ar, "require_org_admin", lambda request, org_id: _CALLER_EMAIL)

    now = _now()
    _seed(
        db_session,
        SessionRow(id="s1", user=_CALLER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=5.0),
        SessionRow(id="s2", user=_OTHER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=3.0),
        SessionRow(id="s3", user="carol@x.io", started_at=now, org_id=_ORG_ID, cost_usd=1.0),
    )
    resp = client.get(f"/api/v1/analytics/tokens?org_id={_ORG_ID}")
    assert resp.status_code == 200
    org = resp.json()["org"]
    assert len(org["top_spenders"]) == 1
    assert {s["user"] for s in org["by_dev"]} == {_CALLER_EMAIL, _OTHER_EMAIL, "carol@x.io"}
    assert [s["cost_usd"] for s in org["by_dev"]] == [5.0, 3.0, 1.0]


def test_org_scope_by_project_groups_by_vcs_provider_never_raw_remote(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    import apps.api.api.routers.receipt.analytics_router as ar

    monkeypatch.setattr(ar, "require_org_admin", lambda request, org_id: _CALLER_EMAIL)

    now = _now()
    _seed(
        db_session,
        SessionRow(
            id="s1", user=_CALLER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=2.0,
            git_remote="git@github.com:acme/private-repo.git",
        ),
        SessionRow(
            id="s2", user=_OTHER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=3.0,
            git_remote="https://github.com/acme/other-repo.git",
        ),
        SessionRow(
            id="s3", user="carol@x.io", started_at=now, org_id=_ORG_ID, cost_usd=1.0,
            git_remote="https://gitlab.com/acme/thing.git",
        ),
        SessionRow(id="s4", user="dave@x.io", started_at=now, org_id=_ORG_ID, cost_usd=0.5, git_remote=None),
    )
    resp = client.get(f"/api/v1/analytics/tokens?org_id={_ORG_ID}")
    assert resp.status_code == 200
    by_project = {p["vcs"]: p for p in resp.json()["org"]["by_project"]}
    assert set(by_project) == {"github", "gitlab", None}
    assert by_project["github"]["cost_usd"] == 5.0
    assert by_project["gitlab"]["cost_usd"] == 1.0
    assert by_project[None]["cost_usd"] == 0.5
    # never the raw remote / owner-repo path anywhere in the response
    raw = resp.text
    assert "acme" not in raw
    assert "private-repo" not in raw


# ---------- GET /analytics/tokens/sessions (8c5d82ad) ----------


def test_sessions_own_scope_always_returned_no_gate(client: TestClient, db_session: SQLSession) -> None:
    now = _now()
    _seed(
        db_session,
        SessionRow(id="s1", user=_CALLER_EMAIL, started_at=now, cost_usd=1.5),
        SessionRow(id="s2", user=_OTHER_EMAIL, started_at=now, cost_usd=9.0),
    )
    resp = client.get("/api/v1/analytics/tokens/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert [i["id"] for i in body["items"]] == ["s1"]


def test_sessions_requires_auth(app: FastAPI, db_session: SQLSession) -> None:
    anon = TestClient(app)
    resp = anon.get("/api/v1/analytics/tokens/sessions")
    assert resp.status_code == 401


def test_sessions_org_scope_gated_by_require_org_admin(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    from fastapi import HTTPException

    import apps.api.api.routers.receipt.analytics_router as ar

    def _deny(request, org_id):
        raise HTTPException(status_code=403, detail="Owner or admin role required for this action")

    monkeypatch.setattr(ar, "require_org_admin", _deny)
    resp = client.get(f"/api/v1/analytics/tokens/sessions?org_id={_ORG_ID}")
    assert resp.status_code == 403


def test_sessions_org_scope_filters_by_dev_and_project(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    import apps.api.api.routers.receipt.analytics_router as ar

    monkeypatch.setattr(ar, "require_org_admin", lambda request, org_id: _CALLER_EMAIL)

    now = _now()
    _seed(
        db_session,
        SessionRow(
            id="s1", user=_CALLER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=1.0,
            git_remote="https://github.com/acme/a.git",
        ),
        SessionRow(
            id="s2", user=_OTHER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=2.0,
            git_remote="https://gitlab.com/acme/b.git",
        ),
        SessionRow(
            id="s3", user=_CALLER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=3.0,
            git_remote="https://gitlab.com/acme/c.git",
        ),
        # different org — must never leak in
        SessionRow(id="s4", user="eve@other.io", started_at=now, org_id="org-other", cost_usd=99.0),
    )

    resp = client.get(f"/api/v1/analytics/tokens/sessions?org_id={_ORG_ID}&dev={_CALLER_EMAIL}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {i["id"] for i in body["items"]} == {"s1", "s3"}

    resp = client.get(f"/api/v1/analytics/tokens/sessions?org_id={_ORG_ID}&project=gitlab")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {i["id"] for i in body["items"]} == {"s2", "s3"}

    resp = client.get(f"/api/v1/analytics/tokens/sessions?org_id={_ORG_ID}&dev={_CALLER_EMAIL}&project=gitlab")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "s3"

    # sorted cost_usd desc when unfiltered
    resp = client.get(f"/api/v1/analytics/tokens/sessions?org_id={_ORG_ID}")
    body = resp.json()
    assert body["total"] == 3
    assert [i["id"] for i in body["items"]] == ["s3", "s2", "s1"]


def test_sessions_limit_caps_items_but_total_reflects_full_count(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    import apps.api.api.routers.receipt.analytics_router as ar

    monkeypatch.setattr(ar, "require_org_admin", lambda request, org_id: _CALLER_EMAIL)

    now = _now()
    _seed(
        db_session,
        *[
            SessionRow(id=f"s{i}", user=_CALLER_EMAIL, started_at=now, org_id=_ORG_ID, cost_usd=float(i))
            for i in range(5)
        ],
    )
    resp = client.get(f"/api/v1/analytics/tokens/sessions?org_id={_ORG_ID}&limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["items"][0]["cost_usd"] == 4.0  # sorted desc


def test_sessions_limit_out_of_range_rejected(client: TestClient, db_session: SQLSession) -> None:
    assert client.get("/api/v1/analytics/tokens/sessions?limit=0").status_code == 422
    assert client.get("/api/v1/analytics/tokens/sessions?limit=201").status_code == 422
