from __future__ import annotations

from yoru_contract import (
    DeviceCodeApproveRequest,
    DeviceCodePollRequest,
    DeviceCodePollResponse,
    DeviceCodeStartRequest,
    DeviceCodeStartResponse,
)


def test_device_code_start_round_trip() -> None:
    req = DeviceCodeStartRequest(label="mac-air · darwin", hostname="mac-air")
    restored = DeviceCodeStartRequest.model_validate_json(req.model_dump_json())
    assert restored == req

    resp = DeviceCodeStartResponse(
        device_code="dc-1",
        user_code="ABCD-EFGH",
        verification_uri="http://x/cli/pair",
        verification_uri_complete="http://x/cli/pair?code=ABCD-EFGH",
        expires_in=600,
        interval=2,
    )
    assert DeviceCodeStartResponse.model_validate_json(resp.model_dump_json()) == resp


def test_device_code_start_request_fields_optional() -> None:
    req = DeviceCodeStartRequest()
    assert req.label is None
    assert req.hostname is None


def test_device_code_poll_round_trip() -> None:
    req = DeviceCodePollRequest(device_code="dc-1")
    assert DeviceCodePollRequest.model_validate_json(req.model_dump_json()) == req

    resp = DeviceCodePollResponse(status="approved", token="rcpt_u_abc")
    assert DeviceCodePollResponse.model_validate_json(resp.model_dump_json()) == resp


def test_device_code_approve_round_trip() -> None:
    req = DeviceCodeApproveRequest(user_code="ABCD-EFGH")
    assert DeviceCodeApproveRequest.model_validate_json(req.model_dump_json()) == req
