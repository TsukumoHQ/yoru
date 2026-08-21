"""Unit tests for `require_org_admin`'s cloud (AUTH_PROVIDER=supabase) path.

The self-host path (any authenticated dashboard user = admin) is exercised
end-to-end by test_service_token.py. The cloud path talks to a real Supabase
client normally — here it's faked out so the membership-check + super-admin
bypass (DEC-yoru-rbac-ruling-1 Q1) logic can be unit-tested in isolation.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from apps.api.api.routers.receipt import auth_router as ar

_ORG_ID = "org-1"
_CALLER_ID = "user-1"
_CALLER_EMAIL = "alice@x.io"


class _FakeAuthClient:
    def get_user(self, _jwt):
        return SimpleNamespace(user=SimpleNamespace(id=_CALLER_ID, email=_CALLER_EMAIL))


class _FakeSupabaseClient:
    def __init__(self):
        self.auth = _FakeAuthClient()


class _FakeSupabaseManager:
    """Stand-in for libs.supabase.supabase.SupabaseManager — `query_records`
    returns canned rows keyed by table so a test can control both the
    `profiles` (super-admin) lookup and the `organization_members` lookup
    independently."""

    def __init__(self, profiles=None, memberships=None, access_token=None):
        self.client = _FakeSupabaseClient()
        self._profiles = profiles or []
        self._memberships = memberships or []

    def query_records(self, table, filters=None):
        if table == "profiles":
            return self._profiles
        if table == "organization_members":
            return self._memberships
        raise AssertionError(f"unexpected table {table!r}")


def _request_with_cookie() -> Request:
    from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME

    scope = {
        "type": "http",
        "headers": [
            (b"cookie", f"{SESSION_COOKIE_NAME}=fake-jwt".encode()),
        ],
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _cloud_auth(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER", "supabase")


def _patch_manager(monkeypatch, profiles=None, memberships=None):
    def _factory(*args, **kwargs):
        return _FakeSupabaseManager(profiles=profiles, memberships=memberships)

    monkeypatch.setattr(
        "libs.supabase.supabase.SupabaseManager", _factory
    )


def test_owner_or_admin_member_authorized(monkeypatch):
    _patch_manager(
        monkeypatch,
        profiles=[{"id": _CALLER_ID, "role": "member"}],
        memberships=[{"org_id": _ORG_ID, "user_id": _CALLER_ID, "role": "owner"}],
    )
    email = ar.require_org_admin(_request_with_cookie(), _ORG_ID)
    assert email == _CALLER_EMAIL


def test_plain_member_denied(monkeypatch):
    _patch_manager(
        monkeypatch,
        profiles=[{"id": _CALLER_ID, "role": "member"}],
        memberships=[{"org_id": _ORG_ID, "user_id": _CALLER_ID, "role": "member"}],
    )
    with pytest.raises(HTTPException) as exc:
        ar.require_org_admin(_request_with_cookie(), _ORG_ID)
    assert exc.value.status_code == 403


def test_non_member_denied(monkeypatch):
    _patch_manager(
        monkeypatch,
        profiles=[{"id": _CALLER_ID, "role": "member"}],
        memberships=[],
    )
    with pytest.raises(HTTPException) as exc:
        ar.require_org_admin(_request_with_cookie(), _ORG_ID)
    assert exc.value.status_code == 403


def test_studio_super_admin_bypasses_membership_check(monkeypatch):
    """DEC-yoru-rbac-ruling-1 Q1: profiles.role=='admin' is authorized for
    ANY org_id, even with zero organization_members rows."""
    _patch_manager(
        monkeypatch,
        profiles=[{"id": _CALLER_ID, "role": "admin"}],
        memberships=[],
    )
    email = ar.require_org_admin(_request_with_cookie(), _ORG_ID)
    assert email == _CALLER_EMAIL


def test_no_session_cookie_401():
    req = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as exc:
        ar.require_org_admin(req, _ORG_ID)
    assert exc.value.status_code == 401


def test_cli_bearer_token_alone_never_authorizes_org_admin(monkeypatch):
    """The CliToken-never-implies-a-role invariant (bdb6fe9d, review-
    backend-api §2): a CliToken presented the normal ingest way (an
    Authorization: Bearer header) carries no session cookie, so it can
    never reach past `require_dashboard_jwt` — org-wide/admin actions stay
    dashboard-cookie-only regardless of what the bearer token could
    otherwise authenticate elsewhere (e.g. event ingest)."""
    # Even with a Supabase manager that WOULD authorize this caller as
    # super-admin, the missing session cookie must still 401 first —
    # proves the gate is cookie-presence-gated, not identity-gated.
    _patch_manager(monkeypatch, profiles=[{"id": _CALLER_ID, "role": "admin"}])
    req = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer rcpt_some_cli_token")],
        }
    )
    with pytest.raises(HTTPException) as exc:
        ar.require_org_admin(req, _ORG_ID)
    assert exc.value.status_code == 401
