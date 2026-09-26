"""Upgrade path for webhook secrets stored in plaintext before vuln-0010.

An install that created webhooks on <= 0.4.0 has raw secrets at rest. After
the Fernet change those rows must keep signing deliveries and get encrypted
in place, exactly once.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from apps.api.api.models.webhooks import WebhookSubscription
from apps.api.api.services.webhook.webhook_secret_crypto import (
    decrypt_secret,
    encrypt_secret,
)
from apps.api.api.services.webhook.webhook_service import WebhookService

LEGACY_HEX = "ab" * 32  # what generate_webhook_secret() stored, unencrypted


@pytest.fixture
def supabase():
    sb = MagicMock()
    sb.get_record = MagicMock()
    sb.update_record = MagicMock()
    sb.client = MagicMock()
    return sb


@pytest.fixture
def service(supabase):
    redis = MagicMock()
    redis.push_to_queue = AsyncMock()
    return WebhookService(supabase=supabase, redis=redis, logger=MagicMock())


def _row(webhook_id, user_id, secret):
    return {
        "id": str(webhook_id),
        "user_id": str(user_id),
        "url": "https://example.com/hook",
        "secret": secret,
        "events": ["user.created"],
    }


@pytest.mark.asyncio
async def test_legacy_plaintext_secret_still_signs_and_is_reencrypted_once(
    service, supabase
):
    wid, uid = uuid.uuid4(), uuid.uuid4()
    supabase.get_record.return_value = _row(wid, uid, LEGACY_HEX)

    raw = await service.get_webhook_secret(webhook_id=wid, user_id=uid)

    assert raw == LEGACY_HEX
    assert supabase.update_record.call_count == 1
    stored = supabase.update_record.call_args.args[2]["secret"]
    assert stored != LEGACY_HEX
    assert decrypt_secret(stored) == LEGACY_HEX

    # Second read sees the now-encrypted row: same secret, no further write.
    supabase.get_record.return_value = _row(wid, uid, stored)
    supabase.update_record.reset_mock()
    assert await service.get_webhook_secret(webhook_id=wid, user_id=uid) == LEGACY_HEX
    supabase.update_record.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_webhook_signs_legacy_plaintext_row_and_reencrypts_it(
    service, supabase
):
    wid = uuid.uuid4()
    response = MagicMock()
    response.data = [
        {
            "id": str(wid),
            "url": "https://example.com/hook",
            "secret": LEGACY_HEX,
            "events": ["user.created"],
        }
    ]
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.contains.return_value = query
    query.execute.return_value = response
    supabase.client.table.return_value = query

    enqueued = await service.trigger_webhook(
        event_name="user.created", data={"id": "u1", "email": "a@b.c"}
    )

    assert enqueued == 1
    job = service.redis.push_to_queue.call_args.args[1]
    assert job["secret"] == LEGACY_HEX
    stored = supabase.update_record.call_args.args[2]["secret"]
    assert decrypt_secret(stored) == LEGACY_HEX


@pytest.mark.asyncio
async def test_ciphertext_from_another_key_is_not_treated_as_legacy_plaintext(
    service, supabase
):
    wid, uid = uuid.uuid4(), uuid.uuid4()
    foreign = Fernet(Fernet.generate_key()).encrypt(b"whsec").decode("ascii")
    supabase.get_record.return_value = _row(wid, uid, foreign)

    with pytest.raises(ValueError):
        await service.get_webhook_secret(webhook_id=wid, user_id=uid)
    supabase.update_record.assert_not_called()


def test_subscription_migration_encrypts_plaintext_rows_and_is_idempotent():
    from apps.api.api.services.webhook.legacy_secret_migration import (
        migrate_subscription_secrets,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    already = encrypt_secret("whsec_already")
    with Session(engine) as s:
        s.add(WebhookSubscription(id="legacy", user="u", url="https://x/y", secret="whsec_legacy"))
        s.add(WebhookSubscription(id="modern", user="u", url="https://x/z", secret=already))
        s.commit()

    with Session(engine) as s:
        assert migrate_subscription_secrets(s) == 1
    with Session(engine) as s:
        assert migrate_subscription_secrets(s) == 0
        rows = {r.id: r.secret for r in s.exec(select(WebhookSubscription)).all()}

    assert decrypt_secret(rows["legacy"]) == "whsec_legacy"
    assert rows["modern"] == already
