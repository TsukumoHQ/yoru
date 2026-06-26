"""Shared pytest fixtures for Receipt v0 router tests.

Isolates each test module with an in-memory SQLite DB by setting
RECEIPT_DB_URL BEFORE the receipt package is imported anywhere.
"""
from __future__ import annotations

import os
from typing import Iterator

import pytest

# Point the receipt DB at in-memory sqlite BEFORE importing the package.
os.environ["RECEIPT_DB_URL"] = "sqlite:///:memory:"

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from apps.api.api.routers.receipt import db as receipt_db  # noqa: E402
from apps.api.api.routers.receipt import models  # noqa: F401,E402

# Register the datastore model (table `datastore_records`) in SQLModel.metadata
# BEFORE create_all, else workspace-routing / share queries hit a missing table
# in tests ("no such table: datastore_records").
from libs.datastore import local_store  # noqa: F401,E402


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
    # local_store did `from ...db import engine` at import time, so it holds a
    # snapshot — rebinding receipt_db.engine alone doesn't reach it. Patch its
    # name too, else datastore-backed paths (workspace routing, share) query a
    # table-less engine ("no such table: datastore_records").
    from libs.datastore import local_store
    old_ls = local_store.engine
    local_store.engine = eng
    try:
        yield eng
    finally:
        receipt_db.engine = old
        local_store.engine = old_ls


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
            from datetime import datetime, timezone
            row.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db_session.add(row)
        db_session.commit()
        return raw, {"Authorization": f"Bearer {raw}"}

    return _mint
