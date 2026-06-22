"""Integration-test harness — hits the live uvicorn pid on :8002.

Fixtures:
  * `client`       — httpx.AsyncClient pointed at RECEIPT_INTEGRATION_URL
                     (default http://localhost:8002). Fails loudly if the
                     backend isn't reachable (no silent skip).
  * `clean_db`     — autouse; truncates events → sessions → hook_tokens
                     before every test so each test sees a pristine DB.
  * `authed_client`— mints a fresh hook-token via
                     POST /api/v1/auth/hook-token and returns
                     (client, user_email, token) with the Bearer header set.

Unit tests live in the parent directory; this package is selected with
`-m integration` and excluded from default runs via pyproject addopts.
"""
from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest
from sqlmodel import SQLModel

from apps.api.api.routers.receipt.db import engine


def _base_url() -> str:
    return os.environ.get("RECEIPT_INTEGRATION_URL", "http://localhost:8002")


def _assert_test_db() -> None:
    """Abort the fixture if we're pointed at a non-test DB.

    The clean_db fixture is destructive (TRUNCATE events/sessions/hook_tokens).
    If pytest is accidentally run with RECEIPT_INTEGRATION_URL targeting a live
    backend whose engine resolves to the production receipt.db, this rails it
    before any data is lost. Safe URLs: `:memory:`, paths containing "test",
    or `RECEIPT_ALLOW_DESTRUCTIVE_TESTS=1` for opt-in.
    """
    url = str(engine.url)
    if os.environ.get("RECEIPT_ALLOW_DESTRUCTIVE_TESTS") == "1":
        return
    if ":memory:" in url:
        return
    if "test" in url.lower():
        return
    raise RuntimeError(
        f"REFUSING to truncate receipt tables — engine URL `{url}` does not "
        f"look like a test DB. Set RECEIPT_DB_URL to something with 'test' in "
        f"the path, use sqlite:///:memory:, or export "
        f"RECEIPT_ALLOW_DESTRUCTIVE_TESTS=1 to override. "
        f"(Guardrail added after a 2026-04-21 incident where integration tests "
        f"wiped the live DB.)"
    )


@pytest.fixture(autouse=True)
def clean_db():
    """Truncate receipt tables before each test.

    Order matters: events has a FK to sessions, so events must go first.
    hook_tokens is independent but listed last to match the brief.
    Untouched: any non-receipt table (alembic, Yoru residue, etc.).

    Guardrail: refuses to run against a DB whose URL doesn't look like a test
    DB (see `_assert_test_db`). This prevents the fixture from truncating the
    production DB when the integration URL points at a live backend.
    """
    _assert_test_db()
    tables = ("events", "sessions", "hook_tokens")
    with engine.begin() as conn:
        for name in tables:
            tbl = SQLModel.metadata.tables.get(name)
            if tbl is not None:
                conn.execute(tbl.delete())
    yield


@pytest.fixture
async def client():
    base_url = _base_url()
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as c:
        try:
            r = await c.get("/health")
        except httpx.TransportError as exc:
            raise RuntimeError(
                f"backend not reachable at {base_url}: {exc}. "
                f"Start it with `make restart-backend` or set RECEIPT_INTEGRATION_URL."
            ) from exc
        if r.status_code != 200:
            raise RuntimeError(
                f"backend not healthy at {base_url}: /health -> {r.status_code}"
            )
        yield c


@pytest.fixture
async def authed_client(client, clean_db):
    """Mint a fresh hook-token for an isolated integ user and attach bearer."""
    user = f"integ-{uuid4().hex[:8]}@test.local"
    r = await client.post("/api/v1/auth/hook-token", json={"user": user})
    r.raise_for_status()
    token = r.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    try:
        yield client, user, token
    finally:
        client.headers.pop("Authorization", None)
