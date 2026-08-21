"""Multi-dev identity model (DEC-yoru-design-ruling-1 A.3#2): CliToken.default_org_id
is a FALLBACK for unrouted events only — never overrides a workspace_repos/
route_rules match, resolved via org_default_workspace_id() (self-host:
deterministic `local:<org_id>`), and fails open (stays None) on any error so a
bad/unreachable org can never break ingest.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlmodel import Session as DBSession

from apps.api.api.routers.receipt import events_router
from apps.api.api.routers.receipt.models import HookToken
from apps.api.api.routers.receipt.models import Session as SessionRow


def _mint(db_session, user: str, default_org_id: str | None):
    raw = f"rcpt_{secrets.token_urlsafe(24)}"
    db_session.add(HookToken(
        id=uuid.uuid4().hex,
        user=user,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        default_org_id=default_org_id,
    ))
    db_session.commit()
    return {"Authorization": f"Bearer {raw}"}


def _event(sid: str, **extra):
    return {"session_id": sid, "kind": "tool_use", "tool": "Bash", **extra}


def test_unrouted_event_falls_back_to_default_org_workspace(client, db_session, engine):
    headers = _mint(db_session, "dev@acme.dev", "org-acme")
    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event("s-fallback")]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    with DBSession(engine) as s:
        sess = s.get(SessionRow, "s-fallback")
        # self-host org_default_workspace_id() is deterministic, no network.
        assert sess.workspace_id == "local:org-acme"


def test_token_without_default_org_id_stays_unrouted(client, db_session, engine):
    headers = _mint(db_session, "dev@acme.dev", None)
    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event("s-no-fallback")]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    with DBSession(engine) as s:
        assert s.get(SessionRow, "s-no-fallback").workspace_id is None


def test_routed_event_never_overridden_by_default_org(client, db_session, engine, monkeypatch):
    # A real workspace_repos match must win outright — default_org_id is a
    # fallback for the UNROUTED case only.
    monkeypatch.setattr(events_router, "_resolve_workspace", lambda **kw: "ws-routed")
    headers = _mint(db_session, "dev@acme.dev", "org-acme")
    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event("s-routed")]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    with DBSession(engine) as s:
        assert s.get(SessionRow, "s-routed").workspace_id == "ws-routed"


def test_default_org_resolution_failure_does_not_break_ingest(client, db_session, engine, monkeypatch):
    def _boom(org_id: str) -> str:
        raise RuntimeError("unreachable org")

    monkeypatch.setattr(events_router, "org_default_workspace_id", _boom)
    headers = _mint(db_session, "dev@acme.dev", "org-acme")
    r = client.post(
        "/api/v1/sessions/events",
        json={"events": [_event("s-fallback-fails")]},
        headers=headers,
    )
    assert r.status_code == 202, r.text  # ingest still succeeds
    with DBSession(engine) as s:
        assert s.get(SessionRow, "s-fallback-fails").workspace_id is None
