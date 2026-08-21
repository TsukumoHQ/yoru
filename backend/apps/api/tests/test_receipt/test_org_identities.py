"""Tests for GET /api/v1/auth/org/identities (22f98e0a).

RBAC contract under test — same shape as 9be89019/5a72353b: gated behind
`require_org_admin` (self-host: any authenticated dashboard user; cloud:
org owner/admin or studio super-admin), server-side only, never via a
CliToken bearer.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt import auth_router as ar
from apps.api.api.routers.receipt.deps import _naive_utc_now
from apps.api.api.routers.receipt.models import CliToken

_ORG = "acme"


def _login(client: TestClient, session_cookie_for, email: str = "admin@yoru.test") -> None:
    from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME

    client.cookies.set(SESSION_COOKIE_NAME, session_cookie_for(email))


def _seed_token(
    db: SQLSession, token_id: str, user: str, *, token_type: str = "user",
    revoked: bool = False, expires_in: timedelta | None = None,
    identity_label: str | None = None, machine_hostname: str | None = None,
) -> None:
    now = _naive_utc_now()
    db.add(CliToken(
        id=token_id, user=user, token_hash=f"hash-{token_id}", token_type=token_type,
        created_at=now,
        revoked_at=now if revoked else None,
        expires_at=(now + expires_in) if expires_in is not None else None,
        identity_label=identity_label, machine_hostname=machine_hostname,
    ))
    db.commit()


def test_self_host_admin_sees_all_user_identities(
    client: TestClient, db_session: SQLSession, session_cookie_for
) -> None:
    """Self-host is single-tenant: any authenticated dashboard user is
    authorized (same posture as require_org_admin's own self-host branch),
    and every user-type CliToken counts as "in the org" regardless of the
    requested org_id string, since CliToken.org_id is unreliable for user
    tokens (always NULL today, per CliToken's own docstring)."""
    _login(client, session_cookie_for)
    _seed_token(db_session, "t1", "alice@x.io", identity_label="alice-laptop", machine_hostname="mbp")
    _seed_token(db_session, "t2", "bob@x.io", identity_label="bob-desktop")
    _seed_token(db_session, "svc", "service:acme", token_type="service")  # excluded

    resp = client.get(f"/api/v1/auth/org/identities?org_id={_ORG}")
    assert resp.status_code == 200
    items = resp.json()
    assert {i["id"] for i in items} == {"t1", "t2"}
    assert all(i["org_id"] == _ORG for i in items)
    by_id = {i["id"]: i for i in items}
    assert by_id["t1"]["user"] == "alice@x.io"
    assert by_id["t1"]["identity_label"] == "alice-laptop"
    assert by_id["t1"]["machine_hostname"] == "mbp"
    assert by_id["t1"]["status"] == "active"


def test_status_derivation_active_expired_revoked(
    client: TestClient, db_session: SQLSession, session_cookie_for
) -> None:
    _login(client, session_cookie_for)
    _seed_token(db_session, "active", "a@x.io")
    _seed_token(db_session, "expired", "b@x.io", expires_in=timedelta(days=-1))
    _seed_token(db_session, "revoked", "c@x.io", revoked=True)
    # revoked takes precedence over an also-expired token.
    _seed_token(db_session, "revoked-and-expired", "d@x.io", revoked=True, expires_in=timedelta(days=-1))

    resp = client.get(f"/api/v1/auth/org/identities?org_id={_ORG}")
    statuses = {i["id"]: i["status"] for i in resp.json()}
    assert statuses == {
        "active": "active", "expired": "expired", "revoked": "revoked",
        "revoked-and-expired": "revoked",
    }


def test_requires_auth(client: TestClient) -> None:
    resp = client.get(f"/api/v1/auth/org/identities?org_id={_ORG}")
    assert resp.status_code == 401


def test_non_admin_denied(client: TestClient, session_cookie_for, monkeypatch) -> None:
    """Caller IS authenticated but require_org_admin denies — the org-wide
    surface must reject, not silently return colleagues' identities. This
    is the load-bearing BOLA assertion."""
    _login(client, session_cookie_for)

    def _deny(request, org_id):
        raise HTTPException(status_code=403, detail="Owner or admin role required for this action")

    monkeypatch.setattr(ar, "require_org_admin", _deny)

    resp = client.get(f"/api/v1/auth/org/identities?org_id={_ORG}")
    assert resp.status_code == 403


def test_cloud_scoped_to_resolved_org_members(
    client: TestClient, db_session: SQLSession, session_cookie_for, monkeypatch
) -> None:
    """Cloud-shaped path (require_org_admin authorized, org membership
    resolved to a specific email set) — only those members' identities
    come back, proving the multi-member/org-scoping contract without
    needing a live Supabase. Mirrors 9be89019/5a72353b's own
    require_org_admin-monkeypatch test pattern."""
    _login(client, session_cookie_for)
    monkeypatch.setattr(ar, "require_org_admin", lambda request, org_id: "admin@yoru.test")
    monkeypatch.setattr(ar, "_is_local_auth", lambda: False)
    monkeypatch.setattr(
        ar, "_org_member_emails", lambda store, org_id: {"alice@x.io", "bob@x.io"}
    )

    _seed_token(db_session, "t1", "alice@x.io")
    _seed_token(db_session, "t2", "bob@x.io")
    _seed_token(db_session, "t3", "carol@other-org.io")  # not a member — must not leak in

    resp = client.get(f"/api/v1/auth/org/identities?org_id={_ORG}")
    assert resp.status_code == 200
    assert {i["id"] for i in resp.json()} == {"t1", "t2"}


def test_cloud_no_members_returns_empty(
    client: TestClient, db_session: SQLSession, session_cookie_for, monkeypatch
) -> None:
    _login(client, session_cookie_for)
    monkeypatch.setattr(ar, "require_org_admin", lambda request, org_id: "admin@yoru.test")
    monkeypatch.setattr(ar, "_is_local_auth", lambda: False)
    monkeypatch.setattr(ar, "_org_member_emails", lambda store, org_id: set())

    _seed_token(db_session, "t1", "alice@x.io")

    resp = client.get(f"/api/v1/auth/org/identities?org_id={_ORG}")
    assert resp.status_code == 200
    assert resp.json() == []


def test_super_admin_bypass_reaches_endpoint(
    client: TestClient, db_session: SQLSession, session_cookie_for, monkeypatch
) -> None:
    """require_org_admin's studio-super-admin bypass (DEC-yoru-rbac-ruling-1
    Q1, 5a72353b) already unit-tested at the gate level in
    test_require_org_admin.py — here we just confirm the endpoint calls
    the SAME shared gate (not a re-implemented check), by proving a
    successful require_org_admin call reaches this endpoint's data path
    end to end regardless of why it succeeded."""
    _login(client, session_cookie_for)
    called = {}

    def _bypass(request, org_id):
        called["org_id"] = org_id
        return "super-admin@yoru.test"

    monkeypatch.setattr(ar, "require_org_admin", _bypass)
    monkeypatch.setattr(ar, "_is_local_auth", lambda: False)
    monkeypatch.setattr(ar, "_org_member_emails", lambda store, org_id: {"alice@x.io"})
    _seed_token(db_session, "t1", "alice@x.io")

    resp = client.get(f"/api/v1/auth/org/identities?org_id={_ORG}")
    assert resp.status_code == 200
    assert called["org_id"] == _ORG
    assert {i["id"] for i in resp.json()} == {"t1"}
