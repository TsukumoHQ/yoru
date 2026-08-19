"""is_super_admin on GET /auth/session/me (multi-tenant, design 44a3774a §4).

The `me` handler must set user.is_super_admin from the instance role
(get_user_role == 'admin'), default False otherwise, and never let a
role-lookup failure break session-aliveness.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest

from apps.api.api.models.user.user_models import UserResponse
from apps.api.api.routers.auth.cookie_router import CookieAuthRouter


def _user(uid) -> UserResponse:
    return UserResponse(
        id=uid, email="dev@studio.dev",
        created_at=datetime.now(), updated_at=datetime.now(),
    )


class _FakeService:
    def __init__(self, role, *, role_raises: bool = False):
        self._role = role
        self._raises = role_raises

    async def get_user(self, user_id):
        return _user(user_id)

    async def get_user_role(self, user_id):
        if self._raises:
            raise RuntimeError("role backend down")
        return self._role


@pytest.mark.asyncio
async def test_admin_role_sets_super_admin_true():
    r = CookieAuthRouter()
    r._service = _FakeService("admin")
    out = await r.me(user_id=uuid4())
    assert out.user.is_super_admin is True


@pytest.mark.asyncio
async def test_non_admin_role_is_not_super_admin():
    r = CookieAuthRouter()
    r._service = _FakeService("user")
    out = await r.me(user_id=uuid4())
    assert out.user.is_super_admin is False


@pytest.mark.asyncio
async def test_no_role_is_not_super_admin():
    r = CookieAuthRouter()
    r._service = _FakeService(None)
    out = await r.me(user_id=uuid4())
    assert out.user.is_super_admin is False


@pytest.mark.asyncio
async def test_role_lookup_failure_defaults_false_not_500():
    r = CookieAuthRouter()
    r._service = _FakeService("admin", role_raises=True)
    out = await r.me(user_id=uuid4())  # must not raise
    assert out.user.is_super_admin is False
