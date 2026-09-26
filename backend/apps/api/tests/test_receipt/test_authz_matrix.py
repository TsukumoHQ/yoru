"""IDOR regression matrix (task 35d155c4).

Two cross-tenant IDORs reproduced by the sweep, fixed in this PR:

- `/me/workspaces/{workspace_id}/repos*`: any authenticated user could list,
  add, or remove another user's workspace repo mappings (no ownership check —
  the local self-hosted datastore has no RLS to fall back on).
- `POST /subscriptions/{id}/cancel`: any authenticated user could cancel
  another user's or org's subscription by guessing/enumerating a UUID (same
  missing-check-on-local-datastore class).

ROUTE_TABLE is the single source of truth for the matrix: one row per
scoped route/scenario, driving `test_authz_matrix` via
`@pytest.mark.parametrize`. `test_route_table_size_matches_matrix` pins the
row count so the matrix can't silently shrink.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from apps.api.api.dependencies import auth as A
from apps.api.api.routers.me.router import MeRouter
from apps.api.api.routers.subscriptions.router import SubscriptionsRouter
from libs.datastore import get_data_store

ALICE, BOB = uuid.uuid4(), uuid.uuid4()


def h(u: uuid.UUID) -> dict[str, str]:
    return {"x-test-user": str(u)}


@pytest.fixture()
def me_client(engine):
    app = FastAPI()
    app.include_router(MeRouter().get_router(), prefix="/api/v1")

    async def _uid(request: Request):
        return uuid.UUID(request.headers["x-test-user"])

    async def _tok(request: Request):
        return "tok-" + request.headers["x-test-user"]

    app.dependency_overrides[A.get_current_user_id] = _uid
    app.dependency_overrides[A.get_current_user_token] = _tok
    return TestClient(app)


@pytest.fixture()
def subs_client(engine):
    app = FastAPI()
    app.include_router(SubscriptionsRouter().get_router(), prefix="/api/v1")

    async def _uid(request: Request):
        return uuid.UUID(request.headers["x-test-user"])

    async def _tok(request: Request):
        return "tok-" + request.headers["x-test-user"]

    app.dependency_overrides[A.get_current_user_id] = _uid
    app.dependency_overrides[A.get_current_user_token] = _tok
    return TestClient(app)


def _make_subscription(**fields) -> str:
    store = get_data_store()
    row = {
        "plan_id": str(uuid.uuid4()),
        "plan_name": "pro",
        "status": "active",
        "start_date": "2026-01-01T00:00:00+00:00",
        "end_date": None,
        **fields,
    }
    return store.insert_record("subscriptions", row)["id"]


def _add_org_member(org_id: str, user_id: uuid.UUID, role: str) -> None:
    get_data_store().insert_record(
        "organization_members",
        {"org_id": org_id, "user_id": str(user_id), "role": role},
    )


# ---------- ROUTE_TABLE: single source of truth for the matrix ----------
#
# Each row is one scoped route/scenario. `setup(client)` prepares any
# resource the steps need and returns a context dict; `steps` is the ordered
# list of (actor, method, path(ctx), json(ctx)|None, expected_status) calls
# that prove the tenant/ownership boundary.


def _setup_alice_workspace(client) -> dict:
    r = client.post("/api/v1/me/workspaces", json={"name": "alice-ws"}, headers=h(ALICE))
    assert r.status_code == 201, r.text
    return {"workspace_id": r.json()["id"]}


def _setup_alice_workspace_with_repo(client) -> dict:
    ctx = _setup_alice_workspace(client)
    r = client.post(
        f"/api/v1/me/workspaces/{ctx['workspace_id']}/repos",
        json={"host": "github.com", "owner": "a", "repo": "r"},
        headers=h(ALICE),
    )
    assert r.status_code == 201, r.text
    ctx["repo_id"] = r.json()["id"]
    return ctx


def _setup_personal_subscription(_client) -> dict:
    return {"sub_id": _make_subscription(user_id=str(ALICE))}


def _setup_org_subscription_bob_member(_client) -> dict:
    org_id = str(uuid.uuid4())
    sub_id = _make_subscription(org_id=org_id, user_id=str(ALICE))
    _add_org_member(org_id, BOB, "member")
    return {"sub_id": sub_id}


def _setup_org_subscription_alice_admin(_client) -> dict:
    org_id = str(uuid.uuid4())
    sub_id = _make_subscription(org_id=org_id, user_id=str(BOB))
    _add_org_member(org_id, ALICE, "admin")
    return {"sub_id": sub_id}


ROUTE_TABLE = [
    {
        "id": "workspace_repos_list_refuses_other_user",
        "app": "me",
        "setup": _setup_alice_workspace,
        "steps": [
            ("get", lambda c: f"/api/v1/me/workspaces/{c['workspace_id']}/repos", BOB, None, 404),
        ],
    },
    {
        "id": "workspace_repos_add_refuses_other_user",
        "app": "me",
        "setup": _setup_alice_workspace,
        "steps": [
            (
                "post",
                lambda c: f"/api/v1/me/workspaces/{c['workspace_id']}/repos",
                BOB,
                {"host": "github.com", "owner": "bob", "repo": "evil"},
                404,
            ),
        ],
    },
    {
        "id": "workspace_repos_delete_refuses_other_user_then_owner_ok",
        "app": "me",
        "setup": _setup_alice_workspace_with_repo,
        "steps": [
            (
                "delete",
                lambda c: f"/api/v1/me/workspaces/{c['workspace_id']}/repos/{c['repo_id']}",
                BOB,
                None,
                404,
            ),
            # Same-owner happy path is unaffected by the new guard.
            (
                "delete",
                lambda c: f"/api/v1/me/workspaces/{c['workspace_id']}/repos/{c['repo_id']}",
                ALICE,
                None,
                204,
            ),
        ],
    },
    {
        "id": "workspace_patch_still_refuses_other_user",
        "app": "me",
        "setup": _setup_alice_workspace,
        "steps": [
            (
                "patch",
                lambda c: f"/api/v1/me/workspaces/{c['workspace_id']}",
                BOB,
                {"name": "renamed-by-bob"},
                404,
            ),
        ],
    },
    {
        "id": "cancel_subscription_refuses_other_user_personal",
        "app": "subs",
        "setup": _setup_personal_subscription,
        "steps": [
            ("post", lambda c: f"/api/v1/subscriptions/{c['sub_id']}/cancel", BOB, None, 404),
        ],
    },
    {
        "id": "cancel_subscription_refuses_org_member",
        "app": "subs",
        "setup": _setup_org_subscription_bob_member,
        "steps": [
            ("post", lambda c: f"/api/v1/subscriptions/{c['sub_id']}/cancel", BOB, None, 403),
        ],
    },
    {
        "id": "cancel_subscription_allows_org_admin",
        "app": "subs",
        "setup": _setup_org_subscription_alice_admin,
        "steps": [
            # The subscription's own user_id (the creator) is not an org
            # billing role — a plain org admin who never created it can
            # still cancel it.
            ("post", lambda c: f"/api/v1/subscriptions/{c['sub_id']}/cancel", ALICE, None, 200),
        ],
    },
]


def test_route_table_size_matches_matrix() -> None:
    """Pins the matrix row count so it can't silently shrink."""
    assert len(ROUTE_TABLE) == 7


@pytest.mark.parametrize("row", ROUTE_TABLE, ids=[r["id"] for r in ROUTE_TABLE])
def test_authz_matrix(row, me_client, subs_client) -> None:
    client = me_client if row["app"] == "me" else subs_client
    ctx = row["setup"](client)
    for method, path_fn, actor, json_body, expected in row["steps"]:
        call = getattr(client, method)
        kwargs: dict = {"headers": h(actor)}
        if json_body is not None:
            kwargs["json"] = json_body
        resp = call(path_fn(ctx), **kwargs)
        assert resp.status_code == expected, resp.text
        if expected == 200 and row["app"] == "subs":
            assert resp.json()["status"] == "cancelled"
