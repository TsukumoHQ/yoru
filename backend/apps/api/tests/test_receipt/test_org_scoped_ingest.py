"""M2a (multi-tenant, design 44a3774a §4): org-pinned ingest.

A verified token binds (user, org_id); ingest stamps sessions/events/flags with
the token's org_id, falling back to DEFAULT_ORG_ID for anon / org-less tokens.
Non-breaking: anon ingest (the v0 path) still succeeds, just tagged default.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlmodel import Session as DBSession
from sqlmodel import select

from apps.api.api.routers.receipt.models import (
    DEFAULT_ORG_ID,
    Event,
    HookToken,
)
from apps.api.api.routers.receipt.models import Session as SessionRow


def _mint(db_session, user: str, org_id: str | None):
    raw = f"rcpt_{secrets.token_urlsafe(24)}"
    db_session.add(HookToken(
        id=uuid.uuid4().hex,
        user=user,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        org_id=org_id,
    ))
    db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


def _event(sid: str):
    # No body `user` — attribution comes from the verified token (M2b).
    return {"session_id": sid, "kind": "tool_use", "tool": "Bash"}


def test_token_org_id_is_stamped_on_session_and_events(client, db_session, engine):
    headers = _mint(db_session, "dev@acme.dev", "org-acme")
    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event("s-acme")]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    with DBSession(engine) as s:
        sess = s.get(SessionRow, "s-acme")
        assert sess.org_id == "org-acme"
        ev = s.exec(select(Event).where(Event.session_id == "s-acme")).first()
        assert ev.org_id == "org-acme"


def test_orgless_token_falls_back_to_default_org(client, db_session, engine):
    headers = _mint(db_session, "dev@legacy.dev", None)
    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event("s-legacy-tok")]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    with DBSession(engine) as s:
        assert s.get(SessionRow, "s-legacy-tok").org_id == DEFAULT_ORG_ID


def test_anon_ingest_is_rejected_401(client, engine):
    # M2b closed the anon path: no credential → 401, nothing written.
    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event("s-anon")]},
    )
    assert r.status_code == 401, r.text
    with DBSession(engine) as s:
        assert s.get(SessionRow, "s-anon") is None
