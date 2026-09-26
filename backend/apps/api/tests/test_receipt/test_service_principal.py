"""Service tokens are principals (service:<org_id> + scopes), never users
(task f71a86b2, ruling cto-tsukumo 12:02Z ab7cd2c6).

Before: POST /auth/service-token took an unvalidated `user_email` and
`_resolve_token` returned `row.user`, so an org admin could mint a token that
acts as any identity (super-admin included). Rule mutations went through
`require_current_user`, so a hook token bound to an admin's email passed the
admin wall.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session as SQLSession
from sqlmodel import select

from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME
from apps.api.api.routers.receipt.models import CliToken
from apps.api.api.routers.receipt.models import Session as SessionRow
from apps.api.api.services.access import visibility as _V

_ORG = "acme"
_OWNER = "owner@acme.dev"


@pytest.fixture()
def app(engine) -> FastAPI:
    from apps.api.api.routers.receipt.auth_router import AuthRouter
    from apps.api.api.routers.receipt.custom_rules_router import CustomRulesRouter
    from apps.api.api.routers.receipt.db import get_session
    from apps.api.api.routers.receipt.events_router import EventsRouter
    from apps.api.api.routers.receipt.sessions_router import SessionsRouter

    _app = FastAPI()
    for r in (EventsRouter(), SessionsRouter(), AuthRouter(), CustomRulesRouter()):
        _app.include_router(r.get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


class _FakeStore:
    def __init__(self, profiles=None, memberships=None):
        self._profiles = profiles or []
        self._memberships = memberships or []

    def query_records(self, table, filters=None):
        f = filters or {}
        rows = {"profiles": self._profiles, "organization_members": self._memberships}.get(table, [])
        return [r for r in rows if all(r.get(k) == v for k, v in f.items())]

    def get_record(self, table, rid):
        if table == "profiles":
            return next((p for p in self._profiles if p.get("id") == rid), None)
        return None


@pytest.fixture()
def owner_store(monkeypatch):
    store = _FakeStore(
        profiles=[{"id": "u1", "email": _OWNER, "role": "user"}],
        memberships=[{"user_id": "u1", "org_id": _ORG, "role": "owner"}],
    )
    monkeypatch.setattr(_V, "get_data_store", lambda: store)
    return store


def _insert_service_token(engine, *, user: str, scopes, org_id: str = _ORG) -> dict:
    """A service-token row exactly as minted before this change when
    `user_email` was set (`user` = a real identity)."""
    raw = "rcpt_s_" + secrets.token_urlsafe(24)
    with SQLSession(engine) as s:
        s.add(CliToken(
            id=uuid.uuid4().hex, user=user, token_type="service", org_id=org_id,
            workspace_id=f"local:{org_id}", token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            scopes=json.dumps(scopes) if scopes is not None else None,
        ))
        s.commit()
    return {"Authorization": f"Bearer {raw}"}


def _insert_hook_token(engine, user: str) -> dict:
    raw = "rcpt_" + secrets.token_urlsafe(24)
    with SQLSession(engine) as s:
        s.add(CliToken(id=uuid.uuid4().hex, user=user, token_hash=hashlib.sha256(raw.encode()).hexdigest()))
        s.commit()
    return {"Authorization": f"Bearer {raw}"}


def _event(session_id: str) -> dict:
    return {"events": [{"session_id": session_id, "kind": "tool_use", "tool": "Bash", "content": "echo hi"}]}


_RULE = {"name": "no-force-push", "match_type": "contains", "pattern": "push --force", "severity": "high"}


# ── AC1: minting ─────────────────────────────────────────────────────────────

def test_service_token_mint_rejects_user_email(client: TestClient, session_cookie_for, engine) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, session_cookie_for("admin@acme.dev"))
    r = client.post(
        "/api/v1/auth/service-token",
        json={"org_id": _ORG, "label": "impersonate", "user_email": "root@studio.dev"},
    )
    assert r.status_code == 422, r.text
    with SQLSession(engine) as s:
        assert s.exec(select(CliToken).where(CliToken.token_type == "service")).all() == []


def test_service_token_mint_principal_is_service_org(client: TestClient, session_cookie_for, engine) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, session_cookie_for("admin@acme.dev"))
    r = client.post("/api/v1/auth/service-token", json={"org_id": _ORG, "label": "fleet"})
    assert r.status_code == 201, r.text
    with SQLSession(engine) as s:
        row = s.exec(select(CliToken).where(CliToken.label == "fleet")).one()
    assert row.user == f"service:{_ORG}"
    assert row.org_id == _ORG


class _FakeAuthClient:
    def get_user(self, _jwt):
        return SimpleNamespace(user=SimpleNamespace(id="admin-a", email="admin@a.dev"))


class _FakeManager:
    def __init__(self, *a, **k):
        self.client = SimpleNamespace(auth=_FakeAuthClient())

    def query_records(self, table, filters=None):
        if table == "profiles":
            return [{"id": "admin-a", "role": "member"}]
        if table == "organization_members":
            return [
                {"org_id": "org-a", "user_id": "admin-a", "role": "admin"}
            ] if (filters or {}).get("org_id") == "org-a" else []
        raise AssertionError(table)


def test_admin_of_org_a_cannot_mint_service_token_for_org_b(
    client: TestClient, session_cookie_for, monkeypatch
) -> None:
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")
    monkeypatch.setattr("libs.supabase.supabase.SupabaseManager", _FakeManager)
    client.cookies.set(SESSION_COOKIE_NAME, session_cookie_for("admin@a.dev"))
    r = client.post("/api/v1/auth/service-token", json={"org_id": "org-b", "label": "x"})
    assert r.status_code == 403, r.text


# ── AC2: principal + scope check in _resolve_token ───────────────────────────

def test_legacy_user_email_service_token_resolves_to_service_principal(client, engine) -> None:
    headers = _insert_service_token(engine, user="root@studio.dev", scopes=["events:write"])
    r = client.post("/api/v1/sessions/events", headers=headers, json=_event("s-legacy"))
    assert r.status_code == 202, r.text
    with SQLSession(engine) as s:
        row = s.exec(select(SessionRow).where(SessionRow.id == "s-legacy")).one()
    assert row.user == f"service:{_ORG}"


def test_service_token_with_events_write_can_ingest(client, engine) -> None:
    headers = _insert_service_token(engine, user=f"service:{_ORG}", scopes=["events:write"])
    assert client.post("/api/v1/sessions/events", headers=headers, json=_event("s-ok")).status_code == 202


def test_service_token_without_events_write_gets_403_on_ingest(client, engine) -> None:
    headers = _insert_service_token(engine, user=f"service:{_ORG}", scopes=["events:read"])
    r = client.post("/api/v1/sessions/events", headers=headers, json=_event("s-no"))
    assert r.status_code == 403, r.text


def test_service_token_without_events_read_gets_403_on_read(client, engine) -> None:
    headers = _insert_service_token(engine, user=f"service:{_ORG}", scopes=["events:write"])
    assert client.get("/api/v1/sessions", headers=headers).status_code == 403


def test_service_token_with_events_read_can_read(client, engine) -> None:
    headers = _insert_service_token(engine, user=f"service:{_ORG}", scopes=["events:read"])
    assert client.get("/api/v1/sessions", headers=headers).status_code == 200


def test_service_token_gets_403_on_unmapped_mutation(client, engine) -> None:
    headers = _insert_service_token(
        engine, user=f"service:{_ORG}", scopes=["events:write", "events:read"]
    )
    r = client.post("/api/v1/sessions/s-x/share", headers=headers, json={})
    assert r.status_code == 403, r.text


def test_service_token_mint_rejects_unknown_scope(client: TestClient, session_cookie_for) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, session_cookie_for("admin@acme.dev"))
    r = client.post(
        "/api/v1/auth/service-token",
        json={"org_id": _ORG, "label": "x", "scopes": ["admin"]},
    )
    assert r.status_code == 400, r.text


def test_user_hook_token_is_not_scope_checked(client, engine) -> None:
    headers = _insert_hook_token(engine, "dev@acme.dev")
    assert client.post("/api/v1/sessions/events", headers=headers, json=_event("s-hook")).status_code == 202
    assert client.get("/api/v1/sessions", headers=headers).status_code == 200


# ── AC3: admin rule mutations need the dashboard JWT ─────────────────────────

def _cookie_client(client: TestClient, session_cookie_for, email: str = _OWNER) -> TestClient:
    client.cookies.set(SESSION_COOKIE_NAME, session_cookie_for(email))
    return client


def _make_rule_via_dashboard(client, session_cookie_for) -> str:
    _cookie_client(client, session_cookie_for)
    r = client.post(f"/api/v1/orgs/{_ORG}/red-flag-rules", json=_RULE)
    assert r.status_code == 201, r.text
    client.cookies.clear()
    return r.json()["id"]


def test_rule_create_dashboard_jwt_ok(client, session_cookie_for, owner_store) -> None:
    _cookie_client(client, session_cookie_for)
    assert client.post(f"/api/v1/orgs/{_ORG}/red-flag-rules", json=_RULE).status_code == 201


def test_rule_create_hook_token_403(client, engine, owner_store) -> None:
    headers = _insert_hook_token(engine, _OWNER)
    r = client.post(f"/api/v1/orgs/{_ORG}/red-flag-rules", headers=headers, json=_RULE)
    assert r.status_code == 403, r.text


def test_rule_create_service_token_403(client, engine, owner_store) -> None:
    headers = _insert_service_token(engine, user=_OWNER, scopes=["events:write", "events:read"])
    r = client.post(f"/api/v1/orgs/{_ORG}/red-flag-rules", headers=headers, json=_RULE)
    assert r.status_code == 403, r.text


def test_rule_update_hook_token_403(client, session_cookie_for, engine, owner_store) -> None:
    rule_id = _make_rule_via_dashboard(client, session_cookie_for)
    headers = _insert_hook_token(engine, _OWNER)
    r = client.patch(f"/api/v1/orgs/{_ORG}/red-flag-rules/{rule_id}", headers=headers, json={"enabled": False})
    assert r.status_code == 403, r.text


def test_rule_update_service_token_403(client, session_cookie_for, engine, owner_store) -> None:
    rule_id = _make_rule_via_dashboard(client, session_cookie_for)
    headers = _insert_service_token(engine, user=_OWNER, scopes=["events:write", "events:read"])
    r = client.patch(f"/api/v1/orgs/{_ORG}/red-flag-rules/{rule_id}", headers=headers, json={"enabled": False})
    assert r.status_code == 403, r.text


def test_rule_delete_hook_token_403(client, session_cookie_for, engine, owner_store) -> None:
    rule_id = _make_rule_via_dashboard(client, session_cookie_for)
    headers = _insert_hook_token(engine, _OWNER)
    r = client.delete(f"/api/v1/orgs/{_ORG}/red-flag-rules/{rule_id}", headers=headers)
    assert r.status_code == 403, r.text


def test_rule_delete_service_token_403(client, session_cookie_for, engine, owner_store) -> None:
    rule_id = _make_rule_via_dashboard(client, session_cookie_for)
    headers = _insert_service_token(engine, user=_OWNER, scopes=["events:write", "events:read"])
    r = client.delete(f"/api/v1/orgs/{_ORG}/red-flag-rules/{rule_id}", headers=headers)
    assert r.status_code == 403, r.text


def test_rule_list_still_open_to_hook_token(client, engine, owner_store) -> None:
    headers = _insert_hook_token(engine, _OWNER)
    assert client.get(f"/api/v1/orgs/{_ORG}/red-flag-rules", headers=headers).status_code == 200
