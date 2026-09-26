"""Encrypt webhook subscription secrets that predate vuln-0010, in place.

Idempotent: rows already holding a Fernet token are skipped, so running it on
every startup is a no-op after the first pass."""
from __future__ import annotations

from sqlmodel import Session, select

from apps.api.api.models.webhooks import WebhookSubscription
from apps.api.api.services.webhook.webhook_secret_crypto import (
    encrypt_secret,
    is_encrypted,
)


def migrate_subscription_secrets(session: Session) -> int:
    migrated = 0
    for row in session.exec(select(WebhookSubscription)).all():
        if not is_encrypted(row.secret):
            row.secret = encrypt_secret(row.secret)
            session.add(row)
            migrated += 1
    if migrated:
        session.commit()
    return migrated
