"""Search-by-VCS on the sessions list (?vcs=...) + the derived `vcs` slug on
list items. Mirrors test_sessions.py's app/auth fixtures.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.models import Session as SessionRow

BASE_TS = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def app(engine) -> FastAPI:
    from apps.api.api.routers.receipt.db import get_session
    from apps.api.api.routers.receipt.sessions_router import SessionsRouter

    _app = FastAPI()
    _app.include_router(SessionsRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture()
def alice_headers(mint_token):
    _, h = mint_token("alice")
    return h


def _seed(db_session) -> None:
    rows = [
        SessionRow(
            id="bb1", user="alice", started_at=BASE_TS,
            cost_usd=0.05, flagged=False, flags=[],
            git_remote="git@bitbucket.org:acme/app.git",
        ),
        SessionRow(
            id="gh1", user="alice", started_at=BASE_TS + timedelta(minutes=1),
            cost_usd=0.05, flagged=False, flags=[],
            git_remote="https://github.com/acme/web",
        ),
        SessionRow(
            id="none1", user="alice", started_at=BASE_TS + timedelta(minutes=2),
            cost_usd=0.05, flagged=False, flags=[],
            git_remote=None,
        ),
    ]
    for r in rows:
        db_session.add(r)
    db_session.commit()


def test_filter_by_bitbucket(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get(
        "/api/v1/sessions", params={"vcs": "bitbucket"}, headers=alice_headers
    )
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"bb1"}


def test_filter_by_github_excludes_bitbucket(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get(
        "/api/v1/sessions", params={"vcs": "github"}, headers=alice_headers
    )
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"gh1"}


def test_vcs_slug_surfaced_on_items(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get("/api/v1/sessions", headers=alice_headers)
    by_id = {i["id"]: i["vcs"] for i in resp.json()["items"]}
    assert by_id["bb1"] == "bitbucket"
    assert by_id["gh1"] == "github"
    assert by_id["none1"] is None


def test_unknown_vcs_is_400(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get(
        "/api/v1/sessions", params={"vcs": "mercurial"}, headers=alice_headers
    )
    assert resp.status_code == 400
    assert "unknown vcs" in resp.json()["detail"].lower()


def test_no_vcs_param_returns_all(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get("/api/v1/sessions", headers=alice_headers)
    assert {i["id"] for i in resp.json()["items"]} == {"bb1", "gh1", "none1"}
