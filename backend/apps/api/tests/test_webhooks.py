"""Tests for /api/v1/webhooks (Wave-34 US-23 W1).

Self-contained: sets RECEIPT_DB_URL=sqlite:///:memory: BEFORE the app
imports, mounts only the routers we care about, and overrides get_session
to use a per-test in-memory SQLite engine. Per self-learning §DB-WIPE-INCIDENT,
NEVER touches backend/data/receipt.db.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from typing import Iterator

# CRITICAL: set BEFORE the receipt package is imported anywhere.
os.environ["RECEIPT_DB_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from apps.api.api.routers.receipt import db as receipt_db  # noqa: E402
from apps.api.api.routers.receipt import models as _receipt_models  # noqa: F401, E402
from apps.api.api.models import webhooks as _webhook_models  # noqa: F401, E402
from apps.api.api.routers.receipt.auth_router import AuthRouter  # noqa: E402
from apps.api.api.routers.receipt.db import get_session  # noqa: E402
from apps.api.api.routers.receipt.models import HookToken  # noqa: E402
from apps.api.api.routers.webhooks import WebhooksRouter  # noqa: E402


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    old = receipt_db.engine
    receipt_db.engine = eng
    try:
        yield eng
    finally:
        receipt_db.engine = old


@pytest.fixture()
def app(engine) -> FastAPI:
    _app = FastAPI()
    _app.include_router(AuthRouter().get_router(), prefix="/api/v1")
    _app.include_router(WebhooksRouter().get_router(), prefix="/api/v1")

    def _override() -> Iterator[Session]:
        with Session(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture()
def mint(engine):
    """Factory: mint(user) -> Authorization headers dict for that user."""
    def _mint(user: str) -> dict[str, str]:
        raw = f"rcpt_{secrets.token_urlsafe(24)}"
        token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with Session(engine) as s:
            s.add(HookToken(id=uuid.uuid4().hex, user=user, token_hash=token_hash))
            s.commit()
        return {"Authorization": f"Bearer {raw}"}

    return _mint


def test_create_webhook_happy_path(client, mint):
    h = mint("alice@example.com")
    r = client.post(
        "/api/v1/webhooks",
        headers=h,
        json={
            "url": "https://hooks.example.com/incoming",
            "events_filter": ["session.completed", "session.flagged"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["url"] == "https://hooks.example.com/incoming"
    assert body["events_filter"] == ["session.completed", "session.flagged"]
    assert body["secret"].startswith("whsec_")
    assert len(body["id"]) == 32  # uuid4 hex


def test_secret_stored_encrypted_not_plaintext(client, mint, engine):
    """vuln-0010 (strix pilot bb1f35fc, pinned): the raw secret is returned
    ONCE in the create response, but the DB row must never hold it in
    plaintext — only the encrypted-at-rest value."""
    from apps.api.api.models.webhooks import WebhookSubscription
    from apps.api.api.services.webhook.webhook_secret_crypto import decrypt_secret

    h = mint("alice@example.com")
    r = client.post(
        "/api/v1/webhooks",
        headers=h,
        json={
            "url": "https://hooks.example.com/incoming",
            "events_filter": ["session.completed"],
        },
    )
    assert r.status_code == 201, r.text
    raw_secret = r.json()["secret"]
    webhook_id = r.json()["id"]

    with Session(engine) as s:
        row = s.get(WebhookSubscription, webhook_id)
        assert row is not None
        assert row.secret != raw_secret  # not plaintext at rest
        assert decrypt_secret(row.secret) == raw_secret  # reversible


def test_list_hides_other_users_subscriptions(client, mint):
    alice = mint("alice@example.com")
    bob = mint("bob@example.com")

    # Alice creates two
    for _ in range(2):
        r = client.post(
            "/api/v1/webhooks",
            headers=alice,
            json={
                "url": "https://hooks.example.com/a",
                "events_filter": ["session.completed"],
            },
        )
        assert r.status_code == 201

    # Bob creates one
    r = client.post(
        "/api/v1/webhooks",
        headers=bob,
        json={
            "url": "https://hooks.example.com/b",
            "events_filter": ["session.flagged"],
        },
    )
    assert r.status_code == 201

    # Alice's list shows only her two; secret is REDACTED.
    r = client.get("/api/v1/webhooks", headers=alice)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    for item in items:
        assert "secret" not in item
        assert item["url"] == "https://hooks.example.com/a"

    # Bob's list shows only his one.
    r = client.get("/api/v1/webhooks", headers=bob)
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["url"] == "https://hooks.example.com/b"


def test_delete_cross_user_returns_404_not_403(client, mint):
    alice = mint("alice@example.com")
    bob = mint("bob@example.com")

    r = client.post(
        "/api/v1/webhooks",
        headers=alice,
        json={
            "url": "https://hooks.example.com/private",
            "events_filter": ["session.completed"],
        },
    )
    assert r.status_code == 201
    alice_webhook_id = r.json()["id"]

    # Bob attempts to delete Alice's webhook — must 404, NOT 403.
    r = client.delete(f"/api/v1/webhooks/{alice_webhook_id}", headers=bob)
    assert r.status_code == 404, (
        f"cross-user delete must return 404 (collapsed guard, no existence "
        f"leak); got {r.status_code}"
    )

    # Alice can still see + delete her webhook (proving Bob's call was a no-op).
    r = client.get("/api/v1/webhooks", headers=alice)
    assert r.status_code == 200
    assert len(r.json()) == 1

    r = client.delete(f"/api/v1/webhooks/{alice_webhook_id}", headers=alice)
    assert r.status_code == 204

    r = client.get("/api/v1/webhooks", headers=alice)
    assert r.json() == []


def test_cap_5_per_user_returns_409(client, mint):
    h = mint("alice@example.com")
    for _ in range(5):
        r = client.post(
            "/api/v1/webhooks",
            headers=h,
            json={
                "url": "https://hooks.example.com/x",
                "events_filter": ["session.completed"],
            },
        )
        assert r.status_code == 201

    r = client.post(
        "/api/v1/webhooks",
        headers=h,
        json={
            "url": "https://hooks.example.com/overflow",
            "events_filter": ["session.completed"],
        },
    )
    assert r.status_code == 409
    assert "cap" in r.json()["detail"].lower()
