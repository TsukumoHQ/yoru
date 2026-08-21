"""Contract-drift guard (design doc §C, trovex:4e85331d4fbe4cdd8c3cbf8ee7c6848b).

Asserts the backend's own DeviceCode* pydantic/SQLModel shapes stay
field-compatible with the co-located `yoru_contract` package's pairing wire
types, and that `CanonicalEvent` is importable and round-trips through the
backend's own dependency resolution. Catches drift at build/test time
instead of a live ingest 400 from a version-skewed CLI.

`red_flags.scan_event`/`scan_canonical_event` now retarget onto
`CanonicalEvent` internally (design doc volet B, DEC-yoru-design-ruling-1) —
see `test_canonical_event.py` for that translation + parity coverage. This
file stays focused on the two schemas not silently diverging.
"""
from __future__ import annotations

from datetime import UTC, datetime

from yoru_contract import (
    CanonicalEvent,
    DeviceCodeApproveRequest,
    DeviceCodePollRequest,
    DeviceCodePollResponse,
    DeviceCodeStartRequest,
    DeviceCodeStartResponse,
)

from apps.api.api.routers.receipt import models as backend_models


def _field_names(model_cls) -> set[str]:
    return set(model_cls.model_fields)


def test_device_code_start_request_matches_backend_shape() -> None:
    assert _field_names(DeviceCodeStartRequest) == _field_names(
        backend_models.DeviceCodeStartIn
    )


def test_device_code_start_response_matches_backend_shape() -> None:
    assert _field_names(DeviceCodeStartResponse) == _field_names(
        backend_models.DeviceCodeStartOut
    )


def test_device_code_poll_request_matches_backend_shape() -> None:
    assert _field_names(DeviceCodePollRequest) == _field_names(
        backend_models.DeviceCodePollIn
    )


def test_device_code_poll_response_matches_backend_shape() -> None:
    assert _field_names(DeviceCodePollResponse) == _field_names(
        backend_models.DeviceCodePollOut
    )


def test_device_code_approve_request_matches_backend_shape() -> None:
    assert _field_names(DeviceCodeApproveRequest) == _field_names(
        backend_models.DeviceCodeApproveIn
    )


def test_canonical_event_importable_and_round_trips() -> None:
    event = CanonicalEvent(
        session_id="s",
        ts=datetime(2026, 8, 21, tzinfo=UTC),
        actor={"identity_id": "tok-1"},
        source="independent:git",
    )
    assert CanonicalEvent.model_validate_json(event.model_dump_json()) == event
