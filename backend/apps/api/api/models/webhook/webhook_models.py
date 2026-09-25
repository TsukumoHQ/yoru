"""
Models Pydantic pour le système de webhooks.

Respect des règles :
- api.responses.mdc : BaseModel + from_attributes + UUID
- python.typing.mdc : Type hints complets + | None pour optionnels
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WebhookDeliveryStatus(str, Enum):
    """Status de livraison d'un webhook."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    RETRYING = "retrying"


class WebhookBase(BaseModel):
    """Champs communs base pour webhooks."""

    url: str = Field(
        ...,
        pattern=r"^https?://",
        description="URL du webhook (HTTPS recommandé en production)",
        examples=["https://api.example.com/webhooks/receive"],
    )
    events: list[str] = Field(
        ...,
        min_length=1,
        description="Liste des événements auxquels s'abonner",
        examples=[["user.created", "payment.succeeded"]],
    )
    active: bool = Field(
        default=True,
        description="Webhook actif ou désactivé",
    )


class WebhookCreate(WebhookBase):
    """Model pour création de webhook (secret généré côté serveur)."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "url": "https://api.example.com/webhooks/receive",
                    "events": ["user.created", "payment.succeeded"],
                    "active": True,
                }
            ]
        }
    )


class WebhookUpdate(BaseModel):
    """Model pour mise à jour partielle de webhook."""

    url: str | None = Field(
        None,
        pattern=r"^https?://",
        description="Nouvelle URL du webhook",
    )
    events: list[str] | None = Field(
        None,
        min_length=1,
        description="Nouvelle liste d'événements",
    )
    active: bool | None = Field(
        None,
        description="Activer/désactiver le webhook",
    )


class WebhookResponse(WebhookBase):
    """Model de réponse avec tous les champs DB."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    secret: str | None = Field(
        None,
        description=(
            "Secret HMAC pour vérification des signatures (à stocker côté "
            "client) — présent UNIQUEMENT sur la réponse de création ou de "
            "régénération (vuln-0010); jamais renvoyé sur get/list/update."
        ),
    )
    retry_count: int = Field(
        default=0,
        description="Nombre de tentatives de livraison échouées consécutives",
    )
    last_attempt_at: datetime | None = Field(
        None,
        description="Date de la dernière tentative de livraison",
    )
    last_success_at: datetime | None = Field(
        None,
        description="Date de la dernière livraison réussie",
    )
    last_error: str | None = Field(
        None,
        description="Message d'erreur de la dernière tentative échouée",
    )
    created_at: datetime
    updated_at: datetime


class WebhookListResponse(BaseModel):
    """Liste paginée de webhooks avec métadonnées."""

    items: list[WebhookResponse]
    total: int = Field(..., description="Nombre total de webhooks")
    page: int = Field(..., ge=1, description="Page courante (1-indexed)")
    page_size: int = Field(..., ge=1, le=200, description="Items par page")
    total_pages: int = Field(..., description="Nombre total de pages")
    has_more: bool = Field(..., description="Y a-t-il une page suivante ?")


class WebhookTestResponse(BaseModel):
    """Réponse du test de webhook."""

    webhook_id: UUID
    status: WebhookDeliveryStatus
    response_code: int | None = Field(
        None,
        description="Code HTTP de réponse (si livré)",
    )
    response_time_ms: float | None = Field(
        None,
        description="Temps de réponse en millisecondes",
    )
    error: str | None = Field(
        None,
        description="Message d'erreur si échec",
    )
