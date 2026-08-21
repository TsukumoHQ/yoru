"""Tests for the group-scoped activity feed (cross-session curated events).

Reuses the visibility wall the sessions list uses: an event only surfaces for a
caller who can see its owning session. Curated to what an agent DID (tool_use /
file_change / error); token/message/session_* noise is skipped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.models import Event
from apps.api.api.routers.receipt.models import Session as SessionRow


BASE_TS = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def app(engine) -> FastAPI:
    from apps.api.api.routers.receipt.activity_router import ActivityRouter
    from apps.api.api.routers.receipt.db import get_session

    _app = FastAPI()
    _app.include_router(ActivityRouter().get_router(), prefix="/api/v1")

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
    # alice owns sa; bob owns sb. Events span curated kinds + noise.
    db_session.add(SessionRow(id="sa", user="alice", agent="claude-code",
                              started_at=BASE_TS))
    db_session.add(SessionRow(id="sb", user="bob", agent="claude-code",
                              started_at=BASE_TS))
    ev = [
        # alice — curated, in time order
        ("sa", "tool_use", "Bash", "npm test", [], 1),
        ("sa", "file_change", "Edit", "src/auth.ts", [], 2),
        ("sa", "tool_use", "Read", "package.json", [], 3),
        ("sa", "error", None, None, [], 4),
        # alice — NOISE that must be filtered out
        ("sa", "message", None, None, [], 5),
        ("sa", "token", None, None, [], 6),
        ("sa", "session_start", None, None, [], 7),
        # bob — curated but NOT visible to alice
        ("sb", "file_change", "Write", ".env.prod", ["secret_aws"], 8),
    ]
    for sid, kind, tool, path, flags, m in ev:
        db_session.add(Event(
            session_id=sid, kind=kind, tool=tool, path=path, flags=flags,
            ts=BASE_TS + timedelta(minutes=m),
        ))
    db_session.commit()


def test_activity_curated_reverse_chron_and_scoped(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get("/api/v1/activity", headers=alice_headers)
    assert resp.status_code == 200
    items = resp.json()["items"]

    # Only alice's CURATED events — bob's session is scoped out, noise dropped.
    assert {i["session_id"] for i in items} == {"sa"}
    assert all(i["kind"] in ("tool_use", "file_change", "error") for i in items)
    # Newest first (error @ +4 leads, Bash @ +1 trails).
    assert [i["kind"] for i in items] == ["error", "tool_use", "file_change", "tool_use"]
    # bob's flagged .env.prod write never appears for alice.
    assert all(i["path"] != ".env.prod" for i in items)
    # Shape: the owning session's user/agent ride on each row.
    assert all(i["user"] == "alice" and i["agent"] == "claude-code" for i in items)


def test_activity_pagination(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get(
        "/api/v1/activity", params={"limit": 2, "offset": 1}, headers=alice_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 2 and body["offset"] == 1
    # Full curated order is [error, tool_use(Read), file_change, tool_use(Bash)];
    # offset 1 limit 2 → the middle two.
    assert [i["kind"] for i in body["items"]] == ["tool_use", "file_change"]


def test_activity_requires_auth(client, db_session):
    _seed(db_session)
    resp = client.get("/api/v1/activity")
    assert resp.status_code == 401


def test_activity_flagged_true_filters_to_flagged_only(client, db_session, alice_headers):
    _seed(db_session)
    db_session.add(Event(
        session_id="sa", kind="file_change", tool="Write", path="secrets.env",
        flags=["secret_generic"], ts=BASE_TS + timedelta(minutes=9),
    ))
    db_session.commit()

    resp = client.get(
        "/api/v1/activity", params={"flagged": "true"}, headers=alice_headers
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["path"] == "secrets.env"
    assert items[0]["flags"] == ["secret_generic"]


def test_activity_flagged_omitted_or_false_unchanged(client, db_session, alice_headers):
    _seed(db_session)
    resp_omitted = client.get("/api/v1/activity", headers=alice_headers)
    resp_false = client.get(
        "/api/v1/activity", params={"flagged": "false"}, headers=alice_headers
    )
    assert resp_omitted.status_code == resp_false.status_code == 200
    assert resp_omitted.json()["items"] == resp_false.json()["items"]
    # Same 4 curated events as test_activity_curated_reverse_chron_and_scoped.
    assert len(resp_omitted.json()["items"]) == 4


def test_activity_flagged_respects_visibility_scope(client, db_session, alice_headers):
    """flagged=true must never surface bob's flagged event to alice — the
    flagged filter applies AFTER the visibility wall, not instead of it."""
    _seed(db_session)
    resp = client.get(
        "/api/v1/activity", params={"flagged": "true"}, headers=alice_headers
    )
    assert resp.status_code == 200
    assert all(i["session_id"] != "sb" for i in resp.json()["items"])
