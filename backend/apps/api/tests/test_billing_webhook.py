"""Unit tests for POST /api/v1/billing/webhook (Stripe).

The Stripe signature verify (`stripe.Webhook.construct_event`) and the Supabase
RPC (`_call_supabase_rpc`) are both stubbed — these tests assert the handler's
event routing, idempotency ledger, and RPC dispatch, not Stripe/Supabase wire
behavior. In-memory SQLite + StaticPool; `webhook.engine` is monkeypatched
because the handler opens its own `DBSession(engine)` (no Depends injection).

Note on current contract: the webhook no longer mutates a local `Org` table —
subscription state is delegated to the Supabase `set/cancel_*` RPCs, and the
local DB keeps only an idempotency ledger (`billing_events`).
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
import stripe

os.environ.setdefault("RECEIPT_DB_URL", "sqlite:///:memory:")

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from apps.api.api.routers.billing import webhook as webhook_module  # noqa: E402
from apps.api.api.routers.billing.models import BillingEvent  # noqa: E402
from apps.api.api.routers.billing.webhook import WebhookRouter  # noqa: E402
from apps.api.api.routers.receipt import db as receipt_db  # noqa: E402
from apps.api.api.routers.receipt import models  # noqa: F401,E402

_SECRET = "whsec_test"
_SIG = {"Stripe-Signature": "t=1,v1=deadbeef", "Content-Type": "application/json"}


@pytest.fixture()
def engine(monkeypatch):
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    # Webhook module imported `engine` at module-load → must monkeypatch the
    # bound name in the webhook namespace, not just receipt_db.engine.
    monkeypatch.setattr(webhook_module, "engine", eng)
    monkeypatch.setattr(receipt_db, "engine", eng)
    yield eng


@pytest.fixture()
def db_session(engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture()
def app(engine) -> FastAPI:
    _app = FastAPI()
    _app.include_router(WebhookRouter().get_router(), prefix="/api/v1/billing")
    return _app


@pytest.fixture()
def secret_env(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", _SECRET)


@pytest.fixture()
def rpc_spy(monkeypatch):
    """Stub the Supabase RPC so no network is hit; record (fn, args) calls."""
    calls: list[tuple[str, dict]] = []

    def _spy(fn, args):
        calls.append((fn, args))

    monkeypatch.setattr(webhook_module, "_call_supabase_rpc", _spy)
    return calls


def _stub_event(monkeypatch, *, raise_sig: bool = False) -> None:
    """Stub stripe.Webhook.construct_event: parse the raw JSON body into a
    stripe.Event-like object (bypassing real signature verification). With
    raise_sig=True it raises SignatureVerificationError (bad/missing sig)."""

    def _construct(payload, sig_header, secret):
        if raise_sig:
            raise stripe.error.SignatureVerificationError("bad sig", sig_header)
        data = json.loads(payload)
        obj = (data.get("data") or {}).get("object") or {}
        return SimpleNamespace(
            id=data.get("id", ""),
            type=data.get("type", ""),
            data=SimpleNamespace(object=obj),
        )

    monkeypatch.setattr(stripe.Webhook, "construct_event", _construct)


def _evt(event_id: str, event_type: str, obj: dict) -> bytes:
    return json.dumps(
        {"id": event_id, "type": event_type, "data": {"object": obj}}
    ).encode("utf-8")


async def _post(app: FastAPI, body: bytes, headers: dict[str, str]) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        return await ac.post("/api/v1/billing/webhook", content=body, headers=headers)


async def test_webhook_checkout_completed_syncs_subscription(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch)
    user_id = str(uuid.uuid4())
    body = _evt(
        "evt_1",
        "checkout.session.completed",
        {
            "client_reference_id": user_id,
            "customer": "cus_1",
            "metadata": {"plan": "team"},
        },
    )

    resp = await _post(app, body, _SIG)
    assert resp.status_code == 200, resp.text

    # Subscription RPC dispatched with the resolved user + plan.
    assert len(rpc_spy) == 1
    fn, args = rpc_spy[0]
    assert fn == "set_user_subscription_from_polar"
    assert args["p_user_id"] == user_id
    assert args["p_plan_name"] == "Team"
    assert args["p_stripe_customer_id"] == "cus_1"

    # Idempotency ledger row written (org_id column carries the user id now).
    evt = db_session.get(BillingEvent, "evt_1")
    assert evt is not None, "billing_events row missing"
    assert evt.event_type == "checkout.session.completed"
    assert evt.org_id == user_id


async def test_webhook_idempotent_on_replay(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch)
    user_id = str(uuid.uuid4())
    body = _evt(
        "evt_1",
        "checkout.session.completed",
        {"client_reference_id": user_id, "metadata": {"plan": "team"}},
    )

    assert (await _post(app, body, _SIG)).status_code == 200
    assert (await _post(app, body, _SIG)).status_code == 200

    # Replay short-circuits on the ledger PK → RPC fired exactly once.
    assert len(rpc_spy) == 1, "replay re-applied the RPC; idempotency broken"
    rows = db_session.exec(
        select(BillingEvent).where(BillingEvent.event_id == "evt_1")
    ).all()
    assert len(rows) == 1, f"expected 1 BillingEvent for evt_1, got {len(rows)}"


async def test_webhook_rejects_bad_signature(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch, raise_sig=True)
    body = _evt(
        "evt_bad_sig",
        "checkout.session.completed",
        {"client_reference_id": str(uuid.uuid4()), "metadata": {"plan": "team"}},
    )

    resp = await _post(
        app, body, {"Stripe-Signature": "t=1,v1=bad", "Content-Type": "application/json"}
    )
    assert resp.status_code == 400, resp.text

    assert rpc_spy == []
    assert db_session.get(BillingEvent, "evt_bad_sig") is None


async def test_webhook_rejects_missing_signature_header(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    # With a real secret configured, Stripe rejects a missing/blank signature.
    _stub_event(monkeypatch, raise_sig=True)
    body = _evt(
        "evt_no_sig",
        "checkout.session.completed",
        {"client_reference_id": str(uuid.uuid4()), "metadata": {"plan": "team"}},
    )

    resp = await _post(app, body, {"Content-Type": "application/json"})
    assert resp.status_code == 400, resp.text

    assert rpc_spy == []
    assert db_session.get(BillingEvent, "evt_no_sig") is None


async def test_webhook_unknown_event_type_is_noop(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch)
    body = _evt("evt_2", "bogus.event.kind", {})

    resp = await _post(app, body, _SIG)
    assert resp.status_code == 200, resp.text

    assert rpc_spy == []
    assert db_session.get(BillingEvent, "evt_2") is None


async def test_subscription_created_syncs_plan(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch)
    user_id = str(uuid.uuid4())
    obj = {
        "metadata": {"user_id": user_id},
        "customer": "cus_9",
        "status": "active",
        # plan is read from the live subscription item's price.nickname.
        "items": {"data": [{"price": {"nickname": "team"}}]},
    }
    body = _evt("evt_sub_created_1", "customer.subscription.created", obj)

    resp = await _post(app, body, _SIG)
    assert resp.status_code == 200, resp.text

    assert len(rpc_spy) == 1
    fn, args = rpc_spy[0]
    assert fn == "set_user_subscription_from_polar"
    assert args["p_user_id"] == user_id
    assert args["p_plan_name"] == "Team"
    assert args["p_status"] == "active"

    evt = db_session.get(BillingEvent, "evt_sub_created_1")
    assert evt is not None
    assert evt.event_type == "customer.subscription.created"
    assert evt.org_id == user_id


async def test_subscription_created_org_plan_via_metadata(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch)
    user_id = str(uuid.uuid4())
    # No items[] array → plan falls back to metadata.plan.
    obj = {"metadata": {"user_id": user_id, "plan": "org"}, "status": "active"}
    body = _evt("evt_sub_created_2", "customer.subscription.created", obj)

    resp = await _post(app, body, _SIG)
    assert resp.status_code == 200, resp.text

    assert len(rpc_spy) == 1
    assert rpc_spy[0][1]["p_plan_name"] == "Org"


async def test_subscription_created_invalid_plan_skips_mutation(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch)
    user_id = str(uuid.uuid4())
    obj = {
        "metadata": {"user_id": user_id, "plan": "enterprise_ultra"},
        "status": "active",
    }
    body = _evt("evt_sub_created_3", "customer.subscription.created", obj)

    resp = await _post(app, body, _SIG)
    # Unknown plan is logged + skipped (no 400): ledger row written, no RPC.
    assert resp.status_code == 200, resp.text
    assert rpc_spy == []
    assert db_session.get(BillingEvent, "evt_sub_created_3") is not None


async def test_subscription_deleted_cancels(
    app, db_session, secret_env, rpc_spy, monkeypatch
) -> None:
    _stub_event(monkeypatch)
    user_id = str(uuid.uuid4())
    obj = {"metadata": {"user_id": user_id}, "status": "canceled"}
    body = _evt("evt_sub_deleted_1", "customer.subscription.deleted", obj)

    resp = await _post(app, body, _SIG)
    assert resp.status_code == 200, resp.text

    assert len(rpc_spy) == 1
    fn, args = rpc_spy[0]
    assert fn == "cancel_user_subscription_from_polar"
    assert args["p_user_id"] == user_id


async def test_webhook_503_when_secret_unset(app, db_session, monkeypatch) -> None:
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    body = _evt("evt_x", "checkout.session.completed", {})
    resp = await _post(app, body, _SIG)
    assert resp.status_code == 503, resp.text
