"""The instance can never end up with zero admins (task 78077378, AC1).

Before: PATCH /admin/instance/users/{id}/role and DELETE had no last-admin
check — demoting or deleting the sole admin locked the instance out with no
recovery path (self-hosted, no support console to re-grant a role).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from apps.api.api.dependencies.auth import require_admin
from apps.api.api.routers.admin.instance import router as instance_router_mod
from apps.api.api.routers.admin.instance.router import InstanceAdminRouter
from apps.api.api.services.auth.local_models import AuthUser


def _mk_user(session: Session, *, role: str) -> AuthUser:
    now = datetime.now(UTC)
    user = AuthUser(
        id=uuid.uuid4().hex,
        email=f"{uuid.uuid4().hex}@instance.test",
        password_hash="x",
        role=role,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@pytest.fixture()
def client(engine, monkeypatch) -> TestClient:
    monkeypatch.setattr(instance_router_mod, "engine", engine)
    app = FastAPI()
    app.include_router(InstanceAdminRouter().get_router(), prefix="/api/v1")
    app.dependency_overrides[require_admin] = lambda: uuid.uuid4()
    return TestClient(app)


def test_demote_last_admin_is_409(client: TestClient, engine) -> None:
    with Session(engine) as s:
        admin = _mk_user(s, role="admin")

    r = client.patch(
        f"/api/v1/admin/instance/users/{admin.id}/role", json={"role": "user"}
    )
    assert r.status_code == 409, r.text

    with Session(engine) as s:
        s.expire_all()
        row = s.get(AuthUser, admin.id)
    assert row.role == "admin"


def test_delete_last_admin_is_409(client: TestClient, engine) -> None:
    with Session(engine) as s:
        admin = _mk_user(s, role="admin")

    r = client.delete(f"/api/v1/admin/instance/users/{admin.id}")
    assert r.status_code == 409, r.text

    with Session(engine) as s:
        assert s.get(AuthUser, admin.id) is not None


def test_demote_one_of_two_admins_ok(client: TestClient, engine) -> None:
    # Separate `with` blocks: a later commit in the same session expires
    # earlier instances, so `first.id` would otherwise need a reload after
    # the session (and thus the test's access to it) has already closed.
    with Session(engine) as s:
        first = _mk_user(s, role="admin")
    first_id = first.id
    with Session(engine) as s:
        _mk_user(s, role="admin")

    r = client.patch(
        f"/api/v1/admin/instance/users/{first_id}/role", json={"role": "user"}
    )
    assert r.status_code == 200, r.text


def test_delete_non_admin_unaffected_by_guard(client: TestClient, engine) -> None:
    with Session(engine) as s:
        _mk_user(s, role="admin")
        member = _mk_user(s, role="user")

    r = client.delete(f"/api/v1/admin/instance/users/{member.id}")
    assert r.status_code == 200, r.text
