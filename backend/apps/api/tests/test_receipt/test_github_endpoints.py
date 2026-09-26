"""Unit tests for the GitHub integration endpoints (me/github_endpoints).

github_endpoints.auto_route_repos is the GitHub twin of
bitbucket_endpoints.auto_route_repos (see test_bitbucket_endpoints.py) — same
require_workspace() ownership guard, fixed in the same commit (bf070d9) as
part of the workspace-repos IDOR. This file pins that guard the same way the
bitbucket suite does, so github_endpoints.py:auto_route_repos is no longer a
silent gap in the authz matrix (strix pilot follow-up, task 35d155c4).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from apps.api.api.routers.me import github_endpoints as gh
from apps.api.api.routers.me import workspaces_endpoints as ws_mod


# ── fakes (mirrors test_bitbucket_endpoints.py) ────────────────────────────
class _FakeExec:
    def __init__(self, data=None):
        self._data = data or []

    def execute(self):
        return type("R", (), {"data": self._data})()


class _FakeTable:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    def insert(self, payload):
        key = (payload.get("host"), payload.get("owner"), payload.get("repo"))
        if key in self._store.existing_repo_keys:
            raise Exception("duplicate key value violates unique constraint")
        self._store.writes.append(("insert", self._name, payload))
        self._store.existing_repo_keys.add(key)
        return _FakeExec()

    def eq(self, *_a, **_k):
        return self

    def select(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._store.mapped_rows})()


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        return _FakeTable(self._store, name)


class FakeStore:
    def __init__(self, workspace_rows=None, mapped_rows=None):
        self._workspace_rows = workspace_rows if workspace_rows is not None else [{"id": "ws1"}]
        self.mapped_rows = mapped_rows or []
        self.existing_repo_keys = set()
        self.writes = []
        self.client = _FakeClient(self)

    def query_records(self, table, filters=None):
        if table == "workspaces":
            rows = self._workspace_rows
            if filters:
                rows = [
                    r for r in rows
                    if all(r.get(k) == v for k, v in filters.items())
                ]
            return list(rows)
        return []


@pytest.fixture()
def patch_store(monkeypatch):
    def _install(store):
        monkeypatch.setattr(gh, "get_data_store", lambda **_k: store)
        # require_workspace (workspaces_endpoints.py) opens its own client —
        # route it at the same fake store so ownership checks see the same
        # workspace rows instead of hitting the real (tableless) datastore.
        monkeypatch.setattr(ws_mod, "get_data_store", lambda **_k: store)
        return store
    return _install


# ── auto-route ───────────────────────────────────────────────────────────
async def test_auto_route_writes_github_host(patch_store):
    uid = uuid4()
    store = patch_store(
        FakeStore(workspace_rows=[{"id": "ws1", "owner_user_id": str(uid)}])
    )
    out = await gh.auto_route_repos(
        "tok", uid,
        gh.AutoRouteIn(workspace_id="ws1", repos=["acme/app", "acme/web"]),
    )
    assert out == {"added": 2, "skipped_already_mapped": 0, "errors": 0}
    inserts = [w for w in store.writes if w[0] == "insert"]
    assert all(w[2]["host"] == "github.com" for w in inserts)
    assert {w[2]["repo"] for w in inserts} == {"app", "web"}


async def test_auto_route_dupes_are_skipped(patch_store):
    uid = uuid4()
    store = patch_store(
        FakeStore(workspace_rows=[{"id": "ws1", "owner_user_id": str(uid)}])
    )
    store.existing_repo_keys.add(("github.com", "acme", "app"))
    out = await gh.auto_route_repos(
        "tok", uid,
        gh.AutoRouteIn(workspace_id="ws1", repos=["acme/app", "acme/web"]),
    )
    assert out["added"] == 1
    assert out["skipped_already_mapped"] == 1


async def test_auto_route_unknown_workspace_is_404(patch_store):
    patch_store(FakeStore(workspace_rows=[]))
    with pytest.raises(gh.HTTPException) as ei:
        await gh.auto_route_repos(
            "tok", uuid4(),
            gh.AutoRouteIn(workspace_id="ghost", repos=["acme/app"]),
        )
    assert ei.value.status_code == 404


async def test_auto_route_refuses_other_users_workspace(patch_store):
    """cross-tenant IDOR pin (mirrors test_bitbucket_endpoints.py): auto-route
    must not accept a workspace_id the caller doesn't own, even if the row
    exists (belongs to someone else). This is the require_workspace() call
    added alongside bitbucket's in bf070d9 — previously untested, so a future
    refactor could drop it silently."""
    owner = uuid4()
    patch_store(
        FakeStore(workspace_rows=[{"id": "ws1", "owner_user_id": str(owner)}])
    )
    with pytest.raises(gh.HTTPException) as ei:
        await gh.auto_route_repos(
            "tok", uuid4(),
            gh.AutoRouteIn(workspace_id="ws1", repos=["acme/app"]),
        )
    assert ei.value.status_code == 404
