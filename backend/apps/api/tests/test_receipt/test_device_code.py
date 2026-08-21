"""Device-pair flow unit tests (issue #54).

Covers the transient-token-store fix that replaced the `!`-sentinel pattern
where the raw token briefly lived in `DeviceAuthorization.token_hash`. After
/approve the raw lives in `DeviceAuthorizationToken` (a dedicated table with
an explicit expires_at), and /poll moves it out and stamps the real sha256
on the pairing row.

Calls the router methods directly rather than via TestClient — the project's
TestClient integration is pinned to a starlette/httpx pair that currently
errors on construction (unrelated to this change; see test_auth.py).
"""
from __future__ import annotations

import hashlib

from apps.api.api.routers.receipt.auth_router import AuthRouter
from apps.api.api.routers.receipt.models import (
    CliToken,
    DeviceAuthorization,
    DeviceAuthorizationToken,
    DeviceCodeApproveIn,
    DeviceCodePollIn,
    DeviceCodeStartIn,
)


class _FakeRequest:
    """Minimal duck-type for Request — device_code_start only reads base_url."""
    base_url = "http://localhost:8000/"
    client = None
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}


def _router() -> AuthRouter:
    return AuthRouter()


def _start(
    router: AuthRouter, db, label: str = "mac-air", hostname: str | None = "mac-air"
) -> tuple[str, str]:
    """Returns (device_code, user_code)."""
    out = router.device_code_start(
        body=DeviceCodeStartIn(label=label, hostname=hostname),
        request=_FakeRequest(),  # type: ignore[arg-type]
        db=db,
    )
    return out.device_code, out.user_code


def _approve(router: AuthRouter, db, user_code: str, user: str = "alice@example.com"):
    return router.device_code_approve(
        body=DeviceCodeApproveIn(user_code=user_code),
        request=_FakeRequest(),  # type: ignore[arg-type]
        db=db,
        current_user=user,
    )


def _poll(router: AuthRouter, db, device_code: str):
    return router.device_code_poll(
        body=DeviceCodePollIn(device_code=device_code),
        request=_FakeRequest(),  # type: ignore[arg-type]
        db=db,
    )


def test_approve_stores_raw_in_transient_table_not_on_pairing_row(db_session) -> None:
    router = _router()
    device_code, user_code = _start(router, db_session)

    _approve(router, db_session, user_code)

    # Pairing row: status=approved, token_hash is the REAL sha256 (no '!' prefix).
    code_hash = hashlib.sha256(device_code.encode()).hexdigest()
    row = db_session.get(DeviceAuthorization, _pairing_id_for(db_session, code_hash))
    assert row.status == "approved"
    assert row.token_hash is not None
    assert not row.token_hash.startswith("!"), "sentinel pattern must be gone"
    assert len(row.token_hash) == 64, "token_hash should be a sha256 hex digest"

    # Transient row exists, carries the raw token, shares TTL with pairing row.
    transient = db_session.get(DeviceAuthorizationToken, code_hash)
    assert transient is not None
    assert transient.raw_token.startswith("rcpt_u_")
    assert transient.expires_at == row.expires_at


def test_poll_after_approve_returns_raw_and_deletes_transient(db_session) -> None:
    router = _router()
    device_code, user_code = _start(router, db_session)
    _approve(router, db_session, user_code)

    result = _poll(router, db_session, device_code)

    assert result.status == "approved"
    assert result.token and result.token.startswith("rcpt_u_")

    # The transient row is GONE — raw token never lingers past the first poll.
    code_hash = hashlib.sha256(device_code.encode()).hexdigest()
    assert db_session.get(DeviceAuthorizationToken, code_hash) is None

    # The pairing row's token_hash matches the raw we just received.
    row = db_session.get(DeviceAuthorization, _pairing_id_for(db_session, code_hash))
    assert row.status == "consumed"
    assert row.token_hash == hashlib.sha256(result.token.encode()).hexdigest()


def test_second_poll_after_consumed_returns_denied(db_session) -> None:
    router = _router()
    device_code, user_code = _start(router, db_session)
    _approve(router, db_session, user_code)

    first = _poll(router, db_session, device_code)
    assert first.status == "approved"

    second = _poll(router, db_session, device_code)
    assert second.status in ("consumed", "denied"), (
        "second poll must not reveal the raw token again"
    )
    assert second.token is None


def test_poll_before_approve_returns_pending(db_session) -> None:
    router = _router()
    device_code, _user_code = _start(router, db_session)

    result = _poll(router, db_session, device_code)

    assert result.status == "pending"
    assert result.token is None


def test_poll_unknown_device_code_returns_expired(db_session) -> None:
    router = _router()
    result = _poll(router, db_session, "not-a-real-code")
    assert result.status == "expired"
    assert result.token is None


def test_approve_populates_machine_hostname_and_identity_label(db_session) -> None:
    # DEC-yoru-design-ruling-1 A.3#1 — the keystone this ticket closes:
    # machine_hostname was always NULL, identity_label didn't exist.
    router = _router()
    _device_code, user_code = _start(
        router, db_session, label="loic-macbook · darwin", hostname="loic-macbook"
    )
    _approve(router, db_session, user_code)

    row = _cli_token_for(db_session, "alice@example.com")
    assert row.machine_hostname == "loic-macbook"
    assert row.identity_label == "loic-macbook · darwin"
    # identity_label round-trips the same value as the legacy `label` field —
    # other CliToken consumers (mint_token, service tokens) keep relying on
    # `label` unchanged.
    assert row.label == row.identity_label


def test_approve_identity_label_defaults_when_no_label_sent(db_session) -> None:
    router = _router()
    _device_code, user_code = _start(router, db_session, label=None, hostname=None)
    _approve(router, db_session, user_code)

    row = _cli_token_for(db_session, "alice@example.com")
    assert row.identity_label == "cli-pair"
    assert row.machine_hostname is None


def _cli_token_for(db, user: str) -> CliToken:
    from sqlmodel import select
    return db.exec(
        select(CliToken).where(CliToken.user == user).order_by(CliToken.created_at.desc())
    ).first()


def _pairing_id_for(db, device_code_hash: str) -> str:
    from sqlmodel import select
    row = db.exec(
        select(DeviceAuthorization).where(
            DeviceAuthorization.device_code_hash == device_code_hash
        )
    ).first()
    assert row is not None
    return row.id
