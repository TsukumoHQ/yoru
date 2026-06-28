"""Unit tests for POST /api/v1/billing/checkout-session (Stripe).

The handler authenticates via `get_current_user_id` (Supabase JWT) — overridden
in-process — and creates a Stripe Checkout Session. The Stripe SDK is never hit:
the happy path stubs `stripe.checkout.Session.create` and points the price-id env
at a dummy so the handler takes the real Stripe branch (not mock mode).
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest

# Point the receipt DB at in-memory sqlite BEFORE importing the package.
os.environ.setdefault("RECEIPT_DB_URL", "sqlite:///:memory:")

import httpx  # noqa: E402
import stripe  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import SQLModel, create_engine  # noqa: E402

from apps.api.api.dependencies.auth import get_current_user_id  # noqa: E402
from apps.api.api.routers.billing.checkout import CheckoutRouter  # noqa: E402
from apps.api.api.routers.receipt import db as receipt_db  # noqa: E402
from apps.api.api.routers.receipt import models  # noqa: F401,E402

USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000c1")


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
    _app.include_router(CheckoutRouter().get_router(), prefix="/api/v1/billing")
    return _app


@pytest.fixture()
def authed_app(app) -> FastAPI:
    """App with the JWT auth dependency overridden to a fixed user."""
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    return app


@pytest.fixture()
def mock_stripe(monkeypatch):
    """Stub stripe.checkout.Session.create and set env so the handler takes the
    real Stripe path (STRIPE_API_KEY + a price-id) rather than mock mode."""
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_dummy")
    monkeypatch.setenv("STRIPE_TEAM_PRICE_ID", "price_team_monthly")
    session = MagicMock(url="https://checkout.stripe.test/c/abc", id="cs_test_abc")
    create = MagicMock(return_value=session)
    monkeypatch.setattr(stripe.checkout.Session, "create", create)
    return create


async def test_checkout_happy_path_team_plan(authed_app, mock_stripe) -> None:
    body = {
        "plan": "team",
        "success_url": "http://localhost/ok",
        "cancel_url": "http://localhost/no",
    }

    transport = httpx.ASGITransport(app=authed_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/billing/checkout-session", json=body)

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["checkout_url"] == "https://checkout.stripe.test/c/abc"
    assert payload["session_id"] == "cs_test_abc"

    assert mock_stripe.call_count == 1
    kwargs = mock_stripe.call_args.kwargs
    # client_reference_id is the authenticated user's UUID — the webhook uses
    # it to resolve which subscription row to upsert on completion.
    assert kwargs["client_reference_id"] == str(USER_ID)
    assert kwargs["success_url"] == "http://localhost/ok"
    assert kwargs["cancel_url"] == "http://localhost/no"


async def test_checkout_invalid_plan_returns_400(authed_app, mock_stripe) -> None:
    body = {
        "plan": "enterprise_ultra",
        "success_url": "http://localhost/ok",
        "cancel_url": "http://localhost/no",
    }

    transport = httpx.ASGITransport(app=authed_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/billing/checkout-session", json=body)

    assert resp.status_code == 400, resp.text
    text = resp.text.lower()
    assert "unknown plan" in text or "invalid plan" in text
    mock_stripe.assert_not_called()


async def test_checkout_unauth_returns_401(app, mock_stripe) -> None:
    # No auth override on `app` → get_current_user_id rejects the request.
    body = {
        "plan": "team",
        "success_url": "http://localhost/ok",
        "cancel_url": "http://localhost/no",
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/v1/billing/checkout-session", json=body)

    assert resp.status_code == 401, resp.text
    mock_stripe.assert_not_called()
