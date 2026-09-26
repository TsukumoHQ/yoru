"""Ingest must not append to a session owned by another identity.

The session id is client-supplied. Before the owner check, any authenticated
identity could POST events into someone else's existing session: the events
joined the victim's hash chain and moved the victim's aggregates and flags.
"""
from __future__ import annotations

from sqlmodel import select

from apps.api.api.routers.receipt.models import Event


def _event(**extra):
    return {"session_id": "s-alice", "kind": "tool_use", "tool": "Bash", **extra}


def _count(db_session, session_id: str) -> int:
    db_session.expire_all()
    return len(db_session.exec(select(Event).where(Event.session_id == session_id)).all())


def test_other_identity_cannot_append_to_existing_session(client, mint_token, db_session):
    _r, alice = mint_token("alice@a.dev")
    _r, bob = mint_token("bob@b.dev")
    first = client.post("/api/v1/sessions/events", json={"events": [_event()]}, headers=alice)
    assert first.status_code == 202, first.text

    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event(content="injected by bob")]},
        headers=bob,
    )

    assert r.status_code == 403, r.text
    assert _count(db_session, "s-alice") == 1


def test_owner_can_keep_appending_to_own_session(client, mint_token, db_session):
    _r, alice = mint_token("alice@a.dev")
    for _ in range(2):
        r = client.post("/api/v1/sessions/events", json={"events": [_event()]}, headers=alice)
        assert r.status_code == 202, r.text
    assert _count(db_session, "s-alice") == 2
