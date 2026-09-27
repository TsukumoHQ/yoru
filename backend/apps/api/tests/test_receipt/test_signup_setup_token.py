"""First-signup-becomes-admin honours SETUP_TOKEN (task 78077378, AC4).

Before: local_provider.sign_up granted `role="admin"` to the first
registered user with no token check at all — SETUP_TOKEN only gated
/setup/init, so plain POST /auth/session/signup was a wide-open side door
to becoming the instance admin on any deployment that set SETUP_TOKEN
specifically to prevent that.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from apps.api.api.routers.auth.cookie_router import CookieAuthRouter
from apps.api.api.services.auth import local_provider as local_provider_mod
from apps.api.api.services.auth.local_models import AuthUser


def _client(engine, monkeypatch) -> TestClient:
    monkeypatch.setattr(local_provider_mod, "engine", engine)
    app = FastAPI()
    app.include_router(CookieAuthRouter().get_router(), prefix="/api/v1")
    return TestClient(app)


def test_first_signup_without_token_is_refused_when_setup_token_set(
    engine, monkeypatch
) -> None:
    monkeypatch.setenv("SETUP_TOKEN", "correct-horse")
    client = _client(engine, monkeypatch)

    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "first@acme.dev", "password": "hunter22222"},
    )
    assert r.status_code == 400, r.text

    with Session(engine) as s:
        assert s.exec(select(AuthUser)).first() is None


def test_first_signup_with_wrong_token_is_refused(engine, monkeypatch) -> None:
    monkeypatch.setenv("SETUP_TOKEN", "correct-horse")
    client = _client(engine, monkeypatch)

    r = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "first@acme.dev",
            "password": "hunter22222",
            "setup_token": "wrong-token",
        },
    )
    assert r.status_code == 400, r.text


def test_first_signup_with_correct_token_becomes_admin(engine, monkeypatch) -> None:
    monkeypatch.setenv("SETUP_TOKEN", "correct-horse")
    client = _client(engine, monkeypatch)

    r = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "first@acme.dev",
            "password": "hunter22222",
            "setup_token": "correct-horse",
        },
    )
    assert r.status_code == 201, r.text

    with Session(engine) as s:
        user = s.exec(select(AuthUser).where(AuthUser.email == "first@acme.dev")).one()
    assert user.role == "admin"


def test_first_signup_without_setup_token_env_is_unaffected(engine, monkeypatch) -> None:
    """No SETUP_TOKEN configured → unchanged behavior: first signup is admin,
    no token required."""
    monkeypatch.delenv("SETUP_TOKEN", raising=False)
    client = _client(engine, monkeypatch)

    r = client.post(
        "/api/v1/auth/signup",
        json={"email": "first@acme.dev", "password": "hunter22222"},
    )
    assert r.status_code == 201, r.text

    with Session(engine) as s:
        user = s.exec(select(AuthUser).where(AuthUser.email == "first@acme.dev")).one()
    assert user.role == "admin"


def test_second_signup_never_needs_setup_token(engine, monkeypatch) -> None:
    """SETUP_TOKEN only gates the FIRST (admin) account — a second signup
    creates a plain 'user' and must not be blocked by it."""
    monkeypatch.setenv("SETUP_TOKEN", "correct-horse")
    client = _client(engine, monkeypatch)

    r1 = client.post(
        "/api/v1/auth/signup",
        json={
            "email": "first@acme.dev",
            "password": "hunter22222",
            "setup_token": "correct-horse",
        },
    )
    assert r1.status_code == 201, r1.text

    r2 = client.post(
        "/api/v1/auth/signup",
        json={"email": "second@acme.dev", "password": "hunter22222"},
    )
    assert r2.status_code == 201, r2.text

    with Session(engine) as s:
        user = s.exec(select(AuthUser).where(AuthUser.email == "second@acme.dev")).one()
    assert user.role == "user"
