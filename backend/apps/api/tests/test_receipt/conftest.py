"""Shared pytest fixtures for Receipt v0 router tests.

Isolates each test module with an in-memory SQLite DB by setting
RECEIPT_DB_URL BEFORE the receipt package is imported anywhere.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# Point the receipt DB at in-memory sqlite BEFORE importing the package.
os.environ["RECEIPT_DB_URL"] = "sqlite:///:memory:"

from datetime import UTC

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from apps.api.api.routers.receipt import db as receipt_db  # noqa: E402
from apps.api.api.routers.receipt import models  # noqa: F401,E402

# Register the datastore model (table `datastore_records`) in SQLModel.metadata
# BEFORE create_all, else workspace-routing / share queries hit a missing table
# in tests ("no such table: datastore_records"). Also rebound per-test (engine
# fixture) onto an isolated engine.
from libs.datastore import local_store  # noqa: E402


@pytest.fixture()
def engine():
    """Fresh in-memory SQLite engine, isolated per test.

    StaticPool + single connection so :memory: survives across sessions.
    """
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    old = receipt_db.engine
    receipt_db.engine = eng
    # The datastore (workspace routing, share) gets its OWN isolated engine —
    # NOT the receipt engine. local_store opens short-lived `with Session(engine)`
    # blocks; on the receipt engine's single StaticPool connection, that nested
    # session's close() rolled back the events router's in-flight flush, so the
    # later summary-rebuild UPDATE matched 0 rows ("StaleDataError"). A separate
    # connection mirrors prod (each Session draws its own pooled connection) and
    # keeps the `datastore_records` table present so queries return [] not raise.
    ds_eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(ds_eng)
    old_ls = local_store.engine
    local_store.engine = ds_eng
    try:
        yield eng
    finally:
        receipt_db.engine = old
        local_store.engine = old_ls
        ds_eng.dispose()


@pytest.fixture()
def db_session(engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture()
def app(engine) -> FastAPI:
    """Minimal FastAPI app mounting only the 3 receipt routers.

    Dev-A imports EventsRouter, Dev-B imports SessionsRouter, Dev-C imports
    SummaryRouter — if a router is missing this fixture will ImportError,
    which is the correct signal.
    """
    from apps.api.api.routers.receipt.auth_router import AuthRouter
    from apps.api.api.routers.receipt.events_router import EventsRouter
    from apps.api.api.routers.receipt.sessions_router import SessionsRouter
    from apps.api.api.routers.receipt.summary_router import SummaryRouter

    _app = FastAPI()
    _app.include_router(EventsRouter().get_router(), prefix="/api/v1")
    _app.include_router(SessionsRouter().get_router(), prefix="/api/v1")
    _app.include_router(SummaryRouter().get_router(), prefix="/api/v1")
    _app.include_router(AuthRouter().get_router(), prefix="/api/v1")

    # Make every get_session() call use the test engine.
    def _override():
        with Session(engine) as s:
            yield s

    from apps.api.api.routers.receipt.db import get_session
    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def mint_token(db_session):
    """Factory: mint_token('alice') → (raw_token, {'Authorization': 'Bearer …'})."""
    import hashlib
    import secrets
    import uuid

    from apps.api.api.routers.receipt.models import HookToken

    def _mint(user: str, revoked: bool = False):
        raw = f"rcpt_{secrets.token_urlsafe(24)}"
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        row = HookToken(id=uuid.uuid4().hex, user=user, token_hash=token_hash)
        if revoked:
            from datetime import datetime
            row.revoked_at = datetime.now(UTC).replace(tzinfo=None)
        db_session.add(row)
        db_session.commit()
        return raw, {"Authorization": f"Bearer {raw}"}

    return _mint


@pytest.fixture()
def ingest_headers(mint_token):
    """Ready bearer headers for ingest. M2b (design 44a3774a §4) closed the
    anonymous ingest path — POST /sessions/events now REQUIRES a credential —
    so tests that just need to write events (not exercise attribution) attach
    these. Matches the event-body `user` most tests use so no 403 mismatch."""
    _raw, headers = mint_token("u-1")
    return headers


@pytest.fixture()
def session_cookie_for():
    """Factory → a valid ``rcpt_session`` access-token JWT for the given email.

    Forges the exact claims ``/auth/signin`` mints (the local provider's access
    token: sub/email/role/iss/iat/exp, HS256 over the process secret) so the
    cookie auth vector resolves a logged-in user — no live signin round-trip.

    Identity flows two ways downstream, both satisfied here:
      - ``require_current_user`` (mint/list/revoke/logout) → ``email_from_token``
        → the **email**, which hook-tokens get bound to.
      - ``get_current_user_id`` (dashboard) → ``verify_access_token`` →
        ``UUID(sub)``.
    The bare test apps mount no ``CsrfMiddleware``, so no ``X-CSRF-Token`` is
    needed — the 401s under test come from the auth dependency, not CSRF.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta

    import jwt

    from apps.api.api.services.auth import local_provider as lp

    def _make(email: str = "alice@yoru.test") -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": _uuid.uuid4().hex,
            "email": email,
            "role": "user",
            "iss": lp._JWT_ISS,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=lp._ACCESS_TTL)).timestamp()),
        }
        return jwt.encode(payload, lp._jwt_secret(), algorithm=lp._JWT_ALG)

    return _make


@pytest.fixture()
def logged_in_client(app, session_cookie_for) -> TestClient:
    """Conftest-app TestClient carrying a valid dashboard session cookie.

    ``client.email`` is the identity hook-tokens get bound to (the v1 mint
    endpoint ignores ``body.user`` and derives the caller from the cookie).
    """
    from apps.api.api.dependencies.auth import SESSION_COOKIE_NAME

    email = "alice@yoru.test"
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE_NAME, session_cookie_for(email))
    c.email = email
    return c
