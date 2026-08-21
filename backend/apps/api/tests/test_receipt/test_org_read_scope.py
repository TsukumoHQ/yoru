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
    emails, orgs, full_org_ids = V.visible_scope_sync("boss@studio.dev")
    assert emails is None and orgs is None  # no restriction, any org
    assert full_org_ids == set()  # moot for super-admin


def test_super_admin_with_valid_org_header_narrows(patch_store):
    patch_store(_FakeStore(
        profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}],
        memberships=[{"user_id": "u1", "org_id": "org-acme"}],
    ))
    emails, orgs, _full = V.visible_scope_sync("boss@studio.dev", "org-acme")
    assert emails is None and orgs == {"org-acme"}


def test_super_admin_org_header_not_a_member_ignored(patch_store):
    patch_store(_FakeStore(profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}]))
    emails, orgs, _full = V.visible_scope_sync("boss@studio.dev", "org-not-mine")
    assert orgs is None  # not a member of it → falls back to all-orgs


def test_member_walled_to_their_orgs(patch_store):
    patch_store(_FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    emails, orgs, full_org_ids = V.visible_scope_sync("dev@acme.dev")
    assert orgs == {"org-acme"}
    assert emails == {"dev@acme.dev"}  # own scope within the org
    assert full_org_ids == set()  # plain 'member' role, no widening


def test_no_profile_falls_back_to_default_org(patch_store):
    patch_store(_FakeStore())  # CLI-only identity, no profile
    emails, orgs, _full = V.visible_scope_sync("cli@x.dev")
    assert orgs == {V._DEFAULT_ORG_ID}
    assert emails == {"cli@x.dev"}


def test_member_with_no_membership_falls_back_to_default_org(patch_store):
    patch_store(_FakeStore(profiles=[{"id": "u3", "email": "solo@x.dev", "role": "user"}]))
    _emails, orgs, _full = V.visible_scope_sync("solo@x.dev")
    assert orgs == {V._DEFAULT_ORG_ID}


# ── per-org owner/admin visibility (4b22046a follow-up, task b153d228) ──────

def test_org_owner_gets_full_org_in_full_org_ids(patch_store):
    patch_store(_FakeStore(
        profiles=[{"id": "u4", "email": "owner@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u4", "org_id": "org-acme", "role": "owner"}],
    ))
    emails, orgs, full_org_ids = V.visible_scope_sync("owner@acme.dev")
    assert orgs == {"org-acme"}
    assert full_org_ids == {"org-acme"}
    assert emails == {"owner@acme.dev"}  # still returned; callers gate on full_org_ids


def test_org_admin_role_also_widens(patch_store):
    patch_store(_FakeStore(
        profiles=[{"id": "u5", "email": "admin@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u5", "org_id": "org-acme", "role": "admin"}],
    ))
    _emails, orgs, full_org_ids = V.visible_scope_sync("admin@acme.dev")
    assert full_org_ids == {"org-acme"}


def test_mixed_role_widens_only_the_owner_org(patch_store):
    """A caller can be owner in org A and a plain member in org B — the
    widen must be per-org, never leak into B."""
    patch_store(_FakeStore(
        profiles=[{"id": "u6", "email": "mixed@x.dev", "role": "user"}],
        memberships=[
            {"user_id": "u6", "org_id": "org-a", "role": "owner"},
            {"user_id": "u6", "org_id": "org-b", "role": "member"},
        ],
    ))
    _emails, orgs, full_org_ids = V.visible_scope_sync("mixed@x.dev")
    assert orgs == {"org-a", "org-b"}
    assert full_org_ids == {"org-a"}  # org-b NOT widened


def test_no_role_row_does_not_widen(patch_store):
    """Sanity: a membership row with no role key (shouldn't happen given the
    schema default, but the resolver must not crash or widen on it)."""
    patch_store(_FakeStore(
        profiles=[{"id": "u7", "email": "norole@x.dev", "role": "user"}],
        memberships=[{"user_id": "u7", "org_id": "org-acme"}],
    ))
    _emails, orgs, full_org_ids = V.visible_scope_sync("norole@x.dev")
    assert orgs == {"org-acme"}
    assert full_org_ids == set()


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


# ── router-level: per-org owner/admin visibility (4b22046a, task b153d228) ──

def test_router_org_owner_sees_every_member_in_own_org(
    client, db_session, mint_token, patch_store
):
    """AC1: an owner of org A sees ALL of org A's sessions, not just own+group."""
    from apps.api.api.routers.receipt.models import Session as SessionRow

    patch_store(_FakeStore(
        profiles=[{"id": "u-owner", "email": "owner@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u-owner", "org_id": "org-acme", "role": "owner"}],
    ))
    _raw, headers = mint_token("owner@acme.dev")
    db_session.add(SessionRow(id="s-own", user="owner@acme.dev", org_id="org-acme", flags=[]))
    db_session.add(SessionRow(id="s-teammate", user="teammate@acme.dev", org_id="org-acme", flags=[]))
    db_session.commit()

    resp = client.get("/api/v1/sessions", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"s-own", "s-teammate"}  # teammate's session visible too


def test_router_org_owner_sees_nothing_from_other_org(
    client, db_session, mint_token, patch_store
):
    """AC2: an owner of org A gets NOTHING from org B — no cross-org reach."""
    from apps.api.api.routers.receipt.models import Session as SessionRow

    patch_store(_FakeStore(
        profiles=[{"id": "u-owner", "email": "owner@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u-owner", "org_id": "org-acme", "role": "owner"}],
    ))
    _raw, headers = mint_token("owner@acme.dev")
    db_session.add(SessionRow(id="s-mine", user="owner@acme.dev", org_id="org-acme", flags=[]))
    db_session.add(SessionRow(id="s-org-b", user="someone@other.dev", org_id="org-other", flags=[]))
    db_session.commit()

    resp = client.get("/api/v1/sessions", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"s-mine"}  # org-other session invisible


def test_router_mixed_role_widens_only_the_owner_org(
    client, db_session, mint_token, patch_store
):
    """The composite filter (session_org_wall_clause) is non-trivial OR/AND
    SQL, not just data — exercise it against real rows: owner in org-a
    (sees every member), plain member in org-b (own+group only, same
    request)."""
    from apps.api.api.routers.receipt.models import Session as SessionRow

    patch_store(_FakeStore(
        profiles=[{"id": "u-mixed", "email": "mixed@x.dev", "role": "user"}],
        memberships=[
            {"user_id": "u-mixed", "org_id": "org-a", "role": "owner"},
            {"user_id": "u-mixed", "org_id": "org-b", "role": "member"},
        ],
    ))
    _raw, headers = mint_token("mixed@x.dev")
    db_session.add(SessionRow(id="a-mine", user="mixed@x.dev", org_id="org-a", flags=[]))
    db_session.add(SessionRow(id="a-teammate", user="teammate@a.dev", org_id="org-a", flags=[]))
    db_session.add(SessionRow(id="b-mine", user="mixed@x.dev", org_id="org-b", flags=[]))
    db_session.add(SessionRow(id="b-teammate", user="teammate@b.dev", org_id="org-b", flags=[]))
    db_session.commit()

    resp = client.get("/api/v1/sessions", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {i["id"] for i in resp.json()["items"]}
    # org-a: owner → both visible. org-b: plain member → own only.
    assert ids == {"a-mine", "a-teammate", "b-mine"}


def test_router_plain_member_stays_own_group_no_regression(
    client, db_session, mint_token, patch_store
):
    """AC3: a plain member (no owner/admin role) stays confined to own+group
    within their org — regression guard against the widen leaking to members."""
    from apps.api.api.routers.receipt.models import Session as SessionRow

    patch_store(_FakeStore(
        profiles=[{"id": "u-member", "email": "member@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u-member", "org_id": "org-acme", "role": "member"}],
    ))
    _raw, headers = mint_token("member@acme.dev")
    db_session.add(SessionRow(id="s-own", user="member@acme.dev", org_id="org-acme", flags=[]))
    db_session.add(SessionRow(id="s-teammate", user="teammate@acme.dev", org_id="org-acme", flags=[]))
    db_session.commit()

    resp = client.get("/api/v1/sessions", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {i["id"] for i in resp.json()["items"]}
    assert ids == {"s-own"}  # teammate's session stays invisible to a plain member
