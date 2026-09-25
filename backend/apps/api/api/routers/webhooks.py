"""Webhook subscription CRUD router — Wave-34 US-23 outbound webhooks (W1).

Routes (mounted at /api/v1 — see main.py):
  - POST   /api/v1/webhooks         create subscription (returns secret ONCE)
  - GET    /api/v1/webhooks         list caller's subscriptions (secret REDACTED)
  - DELETE /api/v1/webhooks/{id}    delete; 404 on cross-user (collapsed guard)

Auth: `require_current_user` (Receipt hook-token bearer). Per-user cap of 5
subscriptions; 409 on overflow. v0 ships per-user only; org_id deferred to v1.5.
"""
from __future__ import annotations

import secrets
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field as PField, HttpUrl
from sqlmodel import Session as DBSession, select

from apps.api.api.models.webhooks import WebhookSubscription, _naive_utcnow
from apps.api.api.routers.receipt.db import get_session
from apps.api.api.routers.receipt.deps import require_current_user
from apps.api.api.services.webhook.webhook_secret_crypto import encrypt_secret

_MAX_PER_USER = 5
_VALID_EVENTS = {"session.completed", "session.flagged"}
EventName = Literal["session.completed", "session.flagged"]


class WebhookCreateIn(BaseModel):
    url: HttpUrl
    events_filter: list[EventName] = PField(min_length=1, max_length=8)


class WebhookCreateOut(BaseModel):
    id: str
    url: str
    events_filter: list[str]
    secret: str  # shown ONCE on create


class WebhookListItem(BaseModel):
    id: str
    url: str
    events_filter: list[str]
    created_at: str
    last_delivery_at: Optional[str] = None
    last_status: Optional[int] = None
    # secret REDACTED — never returned in list


class WebhooksRouter:
    """CRUD for per-user outbound webhook subscriptions."""

    def __init__(self) -> None:
        self.router = APIRouter(prefix="/webhooks", tags=["receipt:webhooks"])
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        self.router.post(
            "",
            response_model=WebhookCreateOut,
            status_code=status.HTTP_201_CREATED,
        )(self.create_webhook)
        self.router.get(
            "",
            response_model=list[WebhookListItem],
        )(self.list_webhooks)
        self.router.delete(
            "/{webhook_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            response_model=None,
        )(self.delete_webhook)

    def create_webhook(
        self,
        body: WebhookCreateIn,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> WebhookCreateOut:
        count = len(
            db.exec(
                select(WebhookSubscription).where(
                    WebhookSubscription.user == current_user
                )
            ).all()
        )
        if count >= _MAX_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"webhook subscription cap reached ({_MAX_PER_USER} per user)",
            )

        secret = "whsec_" + secrets.token_urlsafe(32)
        row = WebhookSubscription(
            user=current_user,
            url=str(body.url),
            secret=encrypt_secret(secret),  # vuln-0010: never store raw
            events_filter=list(body.events_filter),
            created_at=_naive_utcnow(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return WebhookCreateOut(
            id=row.id,
            url=row.url,
            events_filter=row.events_filter,
            secret=secret,  # raw — shown ONCE, here, never again
        )

    def list_webhooks(
        self,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> list[WebhookListItem]:
        rows = db.exec(
            select(WebhookSubscription)
            .where(WebhookSubscription.user == current_user)
            .order_by(WebhookSubscription.created_at.desc())
        ).all()
        return [
            WebhookListItem(
                id=r.id,
                url=r.url,
                events_filter=r.events_filter,
                created_at=r.created_at.isoformat() if r.created_at else "",
                last_delivery_at=(
                    r.last_delivery_at.isoformat() if r.last_delivery_at else None
                ),
                last_status=r.last_status,
            )
            for r in rows
        ]

    def delete_webhook(
        self,
        webhook_id: str,
        db: DBSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
    ) -> None:
        row = db.get(WebhookSubscription, webhook_id)
        # Collapsed guard: 404 (NOT 403) on cross-user — never leak existence.
        if row is None or row.user != current_user:
            raise HTTPException(status_code=404, detail="webhook not found")
        db.delete(row)
        db.commit()
        return None
