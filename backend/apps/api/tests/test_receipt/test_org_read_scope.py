"""M3 (multi-tenant, design 44a3774a §3): org read-scope resolution.

visible_scope_sync is the tenant wall for reads. Unit-tested against a fake
data store so the (super-admin / member / no-profile / X-Organization-Id)
matrix is covered without seeding the whole org layer.
"""
from __future__ import annotations

import pytest

from apps.api.api.services.access import visibility as V


class _FakeStore:
    def __init__(self, profiles=None, memberships=None, group_members=None):
        self._profiles = profiles or []            # list of {id,email,role}
        self._memberships = memberships or []       # {user_id, org_id}
        self._group_members = group_members or []   # {user_id, group_id}

    def query_records(self, table, filters=None):
        f = filters or {}
        if table == "profiles":
            return [p for p in self._profiles
                    if all(p.get(k) == v for k, v in f.items())]
        if table == "organization_members":
            return [m for m in self._memberships
                    if all(m.get(k) == v for k, v in f.items())]
        if table == "user_group_members":
            return [g for g in self._group_members
                    if all(g.get(k) == v for k, v in f.items())]
        return []

    def get_record(self, table, rid):
        if table == "profiles":
            return next((p for p in self._profiles if p.get("id") == rid), None)
        return None


@pytest.fixture()
def patch_store(monkeypatch):
    def _install(store):
        monkeypatch.setattr(V, "get_data_store", lambda: store)
    return _install


def test_super_admin_sees_all_orgs(patch_store):
    patch_store(_FakeStore(profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}]))
    emails, orgs = V.visible_scope_sync("boss@studio.dev")
    assert emails is None and orgs is None  # no restriction, any org


def test_super_admin_with_valid_org_header_narrows(patch_store):
    patch_store(_FakeStore(
        profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}],
        memberships=[{"user_id": "u1", "org_id": "org-acme"}],
    ))
    emails, orgs = V.visible_scope_sync("boss@studio.dev", "org-acme")
    assert emails is None and orgs == {"org-acme"}


def test_super_admin_org_header_not_a_member_ignored(patch_store):
    patch_store(_FakeStore(profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}]))
    emails, orgs = V.visible_scope_sync("boss@studio.dev", "org-not-mine")
    assert orgs is None  # not a member of it → falls back to all-orgs


def test_member_walled_to_their_orgs(patch_store):
    patch_store(_FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    emails, orgs = V.visible_scope_sync("dev@acme.dev")
    assert orgs == {"org-acme"}
    assert emails == {"dev@acme.dev"}  # own scope within the org


def test_no_profile_falls_back_to_default_org(patch_store):
    patch_store(_FakeStore())  # CLI-only identity, no profile
    emails, orgs = V.visible_scope_sync("cli@x.dev")
    assert orgs == {V._DEFAULT_ORG_ID}
    assert emails == {"cli@x.dev"}


def test_member_with_no_membership_falls_back_to_default_org(patch_store):
    patch_store(_FakeStore(profiles=[{"id": "u3", "email": "solo@x.dev", "role": "user"}]))
    _emails, orgs = V.visible_scope_sync("solo@x.dev")
    assert orgs == {V._DEFAULT_ORG_ID}


# ── router-level: the tenant wall + org_id on the payload ────────────────────

def test_list_walls_cross_org_and_surfaces_org_id(client, db_session, mint_token):
    from apps.api.api.routers.receipt.models import Session as SessionRow

    # A CLI-token identity (no profile) → scoped to the default org. Seed one
    # default-org session (NULL org_id counts as default) + one other-org
    # session; only the default one is visible, and it carries org_id.
    _raw, headers = mint_token("u-1")
    db_session.add(SessionRow(id="s-mine", user="u-1", org_id=None, flags=[]))
    db_session.add(SessionRow(id="s-other", user="u-1", org_id="org-acme", flags=[]))
    db_session.commit()

    resp = client.get("/api/v1/sessions", headers=headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    ids = {i["id"] for i in items}
    assert ids == {"s-mine"}                       # org-acme session walled out
    assert "org_id" in items[0]                    # contract seam #4
