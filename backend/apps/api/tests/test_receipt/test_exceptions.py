"""Tests for GET /api/v1/analytics/exceptions (5a72353b).

RBAC contract under test (DEC-yoru-rbac-ruling-1, same shape as 9be89019):
  - `own` (caller's own flagged events) is ALWAYS returned, no gate.
  - `org` is returned ONLY when `org_id` is passed AND the caller clears
    `require_org_admin` — a dev must never pull a colleague's exceptions.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.db import get_session
from apps.api.api.routers.receipt.exceptions_router import ExceptionsRouter
from apps.api.api.routers.receipt.models import EventFlag
from apps.api.api.routers.receipt.models import Session as SessionRow

_CALLER_EMAIL = "alice@x.io"
_OTHER_EMAIL = "bob@x.io"
_ORG_ID = "org-test"


@pytest.fixture()
def app(engine) -> FastAPI:
    """Mount only the ExceptionsRouter — isolated from sibling routers."""
    _app = FastAPI()
    _app.include_router(ExceptionsRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture(autouse=True)
def _patch_scope(monkeypatch):
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


def _seed_session(db: SQLSession, sid: str, user: str, org_id: str | None = None) -> None:
    db.add(SessionRow(id=sid, user=user, started_at=_now(), org_id=org_id))
    db.commit()


def _seed_flag(
    db: SQLSession, session_id: str, rule_id: str, org_id: str | None = None,
    ts: datetime | None = None, category: str = "secret", severity: str = "critical",
) -> None:
    db.add(EventFlag(
        session_id=session_id, org_id=org_id, rule_id=rule_id,
        category=category, severity=severity, ts=ts or _now(),
    ))
    db.commit()


def test_own_scope_always_returned_no_gate(client: TestClient, db_session: SQLSession) -> None:
    _seed_session(db_session, "s1", _CALLER_EMAIL)
    _seed_session(db_session, "s2", _OTHER_EMAIL)
    _seed_flag(db_session, "s1", "secret_generic")
    _seed_flag(db_session, "s2", "shell_destructive")

    resp = client.get("/api/v1/analytics/exceptions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["own"]["totals"] == {"flagged_sessions": 1, "total_flags": 1}
    assert body["own"]["top_rule_ids"] == [{"rule_id": "secret_generic", "count": 1}]
    assert body["org"] is None


def test_org_scope_omitted_without_org_id(client: TestClient, db_session: SQLSession) -> None:
    _seed_session(db_session, "s1", _CALLER_EMAIL)
    _seed_flag(db_session, "s1", "secret_generic")
    resp = client.get("/api/v1/analytics/exceptions")
    assert resp.status_code == 200
    assert resp.json()["org"] is None


def test_org_scope_requires_auth(app: FastAPI, db_session: SQLSession) -> None:
    anon = TestClient(app)
    resp = anon.get(f"/api/v1/analytics/exceptions?org_id={_ORG_ID}")
    assert resp.status_code == 401


def test_org_scope_gated_by_require_org_admin(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    """Caller IS authenticated (own-scope works) but is NOT an org-admin —
    the org-wide surface must reject, not silently return colleagues' data."""
    from fastapi import HTTPException

    import apps.api.api.routers.receipt.exceptions_router as er

    def _deny(request, org_id):
        raise HTTPException(status_code=403, detail="Owner or admin role required for this action")

    monkeypatch.setattr(er, "require_org_admin", _deny)

    _seed_session(db_session, "s1", _OTHER_EMAIL, org_id=_ORG_ID)
    _seed_flag(db_session, "s1", "secret_generic", org_id=_ORG_ID)

    resp = client.get(f"/api/v1/analytics/exceptions?org_id={_ORG_ID}")
    assert resp.status_code == 403


def test_org_scope_returned_when_admin_authorized(
    client: TestClient, db_session: SQLSession, monkeypatch
) -> None:
    """Caller clears require_org_admin — org-wide totals/top_rule_ids/
    top_offenders are returned, scoped to org_id, not bleeding in rows from
    other orgs."""
    import apps.api.api.routers.receipt.exceptions_router as er

    monkeypatch.setattr(er, "require_org_admin", lambda request, org_id: _CALLER_EMAIL)

    _seed_session(db_session, "s1", _CALLER_EMAIL, org_id=_ORG_ID)
    _seed_session(db_session, "s2", _OTHER_EMAIL, org_id=_ORG_ID)
    _seed_session(db_session, "s3", "carol@other-org.io", org_id="org-other")

    _seed_flag(db_session, "s1", "secret_generic", org_id=_ORG_ID)
    _seed_flag(db_session, "s2", "shell_destructive", org_id=_ORG_ID)
    _seed_flag(db_session, "s2", "shell_destructive", org_id=_ORG_ID)
    _seed_flag(db_session, "s3", "secret_generic", org_id="org-other")

    resp = client.get(f"/api/v1/analytics/exceptions?org_id={_ORG_ID}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["org"] is not None
    assert body["org"]["totals"] == {"flagged_sessions": 2, "total_flags": 3}

    rule_counts = {r["rule_id"]: r["count"] for r in body["org"]["top_rule_ids"]}
    assert rule_counts == {"shell_destructive": 2, "secret_generic": 1}
    assert body["org"]["top_rule_ids"][0]["rule_id"] == "shell_destructive"

    offenders = {o["user"]: o for o in body["org"]["top_offenders"]}
    assert set(offenders) == {_CALLER_EMAIL, _OTHER_EMAIL}
    assert offenders[_OTHER_EMAIL] == {
        "user": _OTHER_EMAIL, "flag_count": 2, "flagged_sessions": 1,
    }
    assert offenders[_CALLER_EMAIL] == {
        "user": _CALLER_EMAIL, "flag_count": 1, "flagged_sessions": 1,
    }
    # top_offenders sorted desc by flag_count
    assert body["org"]["top_offenders"][0]["user"] == _OTHER_EMAIL


def test_since_until_range_filters_rows(client: TestClient, db_session: SQLSession) -> None:
    now = _now()
    old = now - timedelta(days=30)
    _seed_session(db_session, "s1", _CALLER_EMAIL)
    _seed_flag(db_session, "s1", "secret_generic", ts=now)
    _seed_flag(db_session, "s1", "shell_destructive", ts=old)

    cutoff = (now - timedelta(days=1)).isoformat() + "Z"
    resp = client.get(f"/api/v1/analytics/exceptions?from={cutoff}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["own"]["totals"] == {"flagged_sessions": 1, "total_flags": 1}
    assert body["own"]["top_rule_ids"] == [{"rule_id": "secret_generic", "count": 1}]


def test_empty_db_returns_zeroed_own_scope(client: TestClient, db_session: SQLSession) -> None:
    resp = client.get("/api/v1/analytics/exceptions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["own"]["totals"] == {"flagged_sessions": 0, "total_flags": 0}
    assert body["own"]["top_rule_ids"] == []
    assert body["org"] is None
