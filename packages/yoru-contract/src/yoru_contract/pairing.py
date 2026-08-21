"""Device-code pairing wire types (design doc §C) — mirrors the shapes
`backend/apps/api/api/routers/receipt/models.py` (Device Code* SQLModel-s)
and `yoru-cli/src/yoru_cli/api.py` (ReceiptClient) already use over HTTP.
Schema only: no auth logic, no persistence.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class DeviceCodeStartRequest(BaseModel):
    label: str | None = Field(default=None, max_length=128)
    hostname: str | None = Field(default=None, max_length=256)


class DeviceCodeStartResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class DeviceCodePollRequest(BaseModel):
    device_code: str = Field(min_length=1)


class DeviceCodePollResponse(BaseModel):
    status: str  # pending|approved|expired|denied
    token: str | None = None
    identity_id: str | None = None


class DeviceCodeApproveRequest(BaseModel):
    user_code: str = Field(min_length=1, max_length=16)
