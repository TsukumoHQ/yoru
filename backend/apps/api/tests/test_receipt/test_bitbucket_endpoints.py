"""Unit tests for the Bitbucket integration endpoints (me/bitbucket_endpoints).

httpx and the datastore are faked so the tests exercise our request-shaping,
error mapping, and workspace_repos writes without hitting Bitbucket or a DB.
asyncio_mode=auto runs the async test functions directly.
"""
from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from apps.api.api.routers.me import bitbucket_endpoints as bb


# ── fakes ────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class _FakeExec:
    def __init__(self, data=None):
        self._data = data or []

    def execute(self):
        return type("R", (), {"data": self._data})()


class _FakeTable:
    def __init__(self, store, name):
        self._store = store
        self._name = name

    # write ops record into store.writes and return an executable
    def upsert(self, payload):
        self._store.writes.append(("upsert", self._name, payload))
        return _FakeExec()

    def insert(self, payload):
        key = (payload.get("host"), payload.get("owner"), payload.get("repo"))
        if key in self._store.existing_repo_keys:
            raise Exception("duplicate key value violates unique constraint")
        self._store.writes.append(("insert", self._name, payload))
        self._store.existing_repo_keys.add(key)
        return _FakeExec()

    def delete(self):
        self._store.writes.append(("delete", self._name, None))
        return self

    def eq(self, *_a, **_k):
        return self

    def select(self, *_a, **_k):
        return self

    def execute(self):
        # only workspace_repos.select(...).eq("host",...).execute() reads here
        return type("R", (), {"data": self._store.mapped_rows})()


class _FakeClient:
    def __init__(self, store):
        self._store = store

    def table(self, name):
        return _FakeTable(self._store, name)


class FakeStore:
    def __init__(self, integration_rows=None, workspace_rows=None,
                 mapped_rows=None):
        self._integration_rows = integration_rows or []
        self._workspace_rows = workspace_rows if workspace_rows is not None else [{"id": "ws1"}]
        self.mapped_rows = mapped_rows or []
        self.existing_repo_keys = set()
        self.writes = []
        self.client = _FakeClient(self)

    def query_records(self, table, filters=None):
        if table == "bitbucket_integrations":
            return list(self._integration_rows)
        if table == "workspaces":
            return list(self._workspace_rows)
        return []


@pytest.fixture()
def patch_store(monkeypatch):
    def _install(store):
        monkeypatch.setattr(bb, "get_data_store", lambda **_k: store)
        return store
    return _install


# ── status ───────────────────────────────────────────────────────────────
async def test_status_not_connected(patch_store):
    patch_store(FakeStore(integration_rows=[]))
    out = await bb.get_bitbucket_status("tok", uuid4())
    assert out.connected is False


async def test_status_connected(patch_store):
    patch_store(FakeStore(integration_rows=[{"bitbucket_username": "acme-dev"}]))
    out = await bb.get_bitbucket_status("tok", uuid4())
    assert out.connected is True and out.bitbucket_username == "acme-dev"


# ── connect ──────────────────────────────────────────────────────────────
async def test_connect_persists_and_returns_username(patch_store, monkeypatch):
    store = patch_store(FakeStore())
    monkeypatch.setattr(
        bb.httpx, "get",
        lambda *a, **k: _FakeResp(200, {"uuid": "{u-1}", "username": "acme-dev"}),
    )
    out = await bb.connect_bitbucket(
        "tok", uuid4(), bb.BitbucketConnectIn(provider_token="x" * 25)
    )
    assert out.connected is True and out.bitbucket_username == "acme-dev"
    assert any(w[0] == "upsert" and w[1] == "bitbucket_integrations"
               for w in store.writes)


async def test_connect_rejects_bad_token(patch_store, monkeypatch):
    patch_store(FakeStore())
    monkeypatch.setattr(bb.httpx, "get", lambda *a, **k: _FakeResp(401, {}))
    with pytest.raises(bb.HTTPException) as ei:
        await bb.connect_bitbucket(
            "tok", uuid4(), bb.BitbucketConnectIn(provider_token="x" * 25)
        )
    assert ei.value.status_code == 401


async def test_connect_bitbucket_unreachable_is_502(patch_store, monkeypatch):
    patch_store(FakeStore())

    def _boom(*a, **k):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(bb.httpx, "get", _boom)
    with pytest.raises(bb.HTTPException) as ei:
        await bb.connect_bitbucket(
            "tok", uuid4(), bb.BitbucketConnectIn(provider_token="x" * 25)
        )
    assert ei.value.status_code == 502


# ── repos ────────────────────────────────────────────────────────────────
async def test_list_repos_requires_connection(patch_store):
    patch_store(FakeStore(integration_rows=[]))
    with pytest.raises(bb.HTTPException) as ei:
        await bb.list_bitbucket_repos("tok", uuid4())
    assert ei.value.status_code == 400


async def test_list_repos_maps_shape_and_workspace(patch_store, monkeypatch):
    patch_store(FakeStore(
        integration_rows=[{"provider_token": "pt"}],
        mapped_rows=[{"workspace_id": "wsX", "host": "bitbucket.org",
                      "owner": "acme", "repo": "app"}],
    ))
    payload = {"values": [
        {"uuid": "{r-1}", "full_name": "acme/app", "name": "app",
         "is_private": True, "updated_on": "2026-01-01T00:00:00Z"},
        {"uuid": "{r-2}", "full_name": "acme/web", "name": "web",
         "is_private": False, "updated_on": "2026-01-02T00:00:00Z"},
    ]}
    monkeypatch.setattr(bb.httpx, "get", lambda *a, **k: _FakeResp(200, payload))
    repos = await bb.list_bitbucket_repos("tok", uuid4())
    assert [r.repo for r in repos] == ["app", "web"]
    assert repos[0].host == "bitbucket.org"
    assert repos[0].owner == "acme"
    assert repos[0].private is True
    assert repos[0].mapped_workspace_id == "wsX"   # already-mapped
    assert repos[1].mapped_workspace_id is None


async def test_list_repos_token_expired_is_401(patch_store, monkeypatch):
    patch_store(FakeStore(integration_rows=[{"provider_token": "pt"}]))
    monkeypatch.setattr(bb.httpx, "get", lambda *a, **k: _FakeResp(401, {}))
    with pytest.raises(bb.HTTPException) as ei:
        await bb.list_bitbucket_repos("tok", uuid4())
    assert ei.value.status_code == 401


# ── auto-route ───────────────────────────────────────────────────────────
async def test_auto_route_writes_bitbucket_host(patch_store):
    store = patch_store(FakeStore(workspace_rows=[{"id": "ws1"}]))
    out = await bb.auto_route_repos(
        "tok", uuid4(),
        bb.AutoRouteIn(workspace_id="ws1", repos=["acme/app", "acme/web"]),
    )
    assert out == {"added": 2, "skipped_already_mapped": 0, "errors": 0}
    inserts = [w for w in store.writes if w[0] == "insert"]
    assert all(w[2]["host"] == "bitbucket.org" for w in inserts)
    assert {w[2]["repo"] for w in inserts} == {"app", "web"}


async def test_auto_route_dupes_are_skipped(patch_store):
    store = patch_store(FakeStore(workspace_rows=[{"id": "ws1"}]))
    store.existing_repo_keys.add(("bitbucket.org", "acme", "app"))
    out = await bb.auto_route_repos(
        "tok", uuid4(),
        bb.AutoRouteIn(workspace_id="ws1", repos=["acme/app", "acme/web"]),
    )
    assert out["added"] == 1
    assert out["skipped_already_mapped"] == 1


async def test_auto_route_unknown_workspace_is_404(patch_store):
    patch_store(FakeStore(workspace_rows=[]))
    with pytest.raises(bb.HTTPException) as ei:
        await bb.auto_route_repos(
            "tok", uuid4(),
            bb.AutoRouteIn(workspace_id="ghost", repos=["acme/app"]),
        )
    assert ei.value.status_code == 404
