"""Tests for POST /api/v1/auth/refresh — AUTH-HARDENING-V1 §6.

The four mandated cases:
  1. test_refresh_happy_rotate      — happy path mints a new row, stamps the old.
  2. test_refresh_expired_401       — expired-not-revoked does NOT kill family.
  3. test_refresh_reuse_revokes_family — presenting a revoked row kills siblings.
  4. test_refresh_unknown_401       — unknown hash 401s without touching the DB.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

# Module-level import so the engine fixture's create_all picks up the table.
from apps.api.api.routers.receipt.auth_sessions_model import AuthSession


def _naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _seed_refresh(
    session: Session,
    user_email: str,
    family_id: str | None = None,
    expires_in: timedelta = timedelta(days=30),
    revoked: bool = False,
) -> tuple[str, str]:
    """Insert an AuthSession row; returns (raw_token, token_hash)."""
    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    now = _naive_now()
    row = AuthSession(
        id=uuid.uuid4().hex,
        user_email=user_email,
        refresh_token_hash=token_hash,
        issued_at=now,
        expires_at=now + expires_in,
        revoked_at=now if revoked else None,
        family_id=family_id or uuid.uuid4().hex,
        parent_token_hash=None,
    )
    session.add(row)
    session.commit()
    return raw, token_hash


def test_refresh_happy_rotate(client: TestClient, db_session: Session) -> None:
    family_id = uuid.uuid4().hex
    raw, old_hash = _seed_refresh(db_session, "alice@example.com", family_id=family_id)

    r = client.post("/api/v1/auth/refresh", cookies={"refresh_token": raw})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["refresh_token"] != raw

    # New row exists in same family, pointing back at the old hash.
    new_hash = hashlib.sha256(body["refresh_token"].encode("utf-8")).hexdigest()
    new_row = db_session.exec(
        select(AuthSession).where(AuthSession.refresh_token_hash == new_hash)
    ).one()
    assert new_row.family_id == family_id
    assert new_row.parent_token_hash == old_hash
    assert new_row.revoked_at is None
    assert new_row.expires_at > _naive_now()

    # Old row stamped: last_used_at first, then revoked_at.
    db_session.expire_all()
    old_row = db_session.exec(
        select(AuthSession).where(AuthSession.refresh_token_hash == old_hash)
    ).one()
    assert old_row.last_used_at is not None
    assert old_row.revoked_at is not None

    # Cookie rotated with the new raw token.
    assert r.cookies.get("refresh_token") == body["refresh_token"]


def test_refresh_cookie_secure_in_production(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    """vuln-0004 (strix pilot bb1f35fc, pinned): this cookie used to hardcode
    secure=False unconditionally — ship it here, outside dev, and it must
    carry Secure (+ HttpOnly, SameSite)."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    raw, _old_hash = _seed_refresh(db_session, "alice@example.com")

    r = client.post("/api/v1/auth/refresh", cookies={"refresh_token": raw})
    assert r.status_code == 200, r.text

    set_cookie_headers = r.headers.get_list("set-cookie")
    refresh_cookie = next(h for h in set_cookie_headers if h.startswith("refresh_token="))
    assert "Secure" in refresh_cookie
    assert "HttpOnly" in refresh_cookie
    assert "samesite=lax" in refresh_cookie.lower()


def test_refresh_expired_401(client: TestClient, db_session: Session) -> None:
    family_id = uuid.uuid4().hex
    # Seed one expired-but-not-revoked row AND one live sibling in the same
    # family — to prove the sibling is NOT touched by the expired-path 401.
    raw, old_hash = _seed_refresh(
        db_session,
        "bob@example.com",
        family_id=family_id,
        expires_in=timedelta(seconds=-1),
    )
    _, sibling_hash = _seed_refresh(
        db_session,
        "bob@example.com",
        family_id=family_id,
        expires_in=timedelta(days=30),
    )

    r = client.post("/api/v1/auth/refresh", cookies={"refresh_token": raw})
    assert r.status_code == 401, r.text
    assert r.json() == {"error": "auth_expired"}

    # Seeded row unchanged — expiry alone does NOT trigger revoke.
    db_session.expire_all()
    old_row = db_session.exec(
        select(AuthSession).where(AuthSession.refresh_token_hash == old_hash)
    ).one()
    assert old_row.revoked_at is None
    # Sibling untouched.
    sibling = db_session.exec(
        select(AuthSession).where(AuthSession.refresh_token_hash == sibling_hash)
    ).one()
    assert sibling.revoked_at is None

    # And no new row was minted.
    count = len(
        db_session.exec(
            select(AuthSession).where(AuthSession.family_id == family_id)
        ).all()
    )
    assert count == 2


def test_refresh_reuse_revokes_family(
    client: TestClient,
    db_session: Session,
    caplog,
) -> None:
    family_id = uuid.uuid4().hex
    raw_a, hash_a = _seed_refresh(
        db_session, "carol@example.com", family_id=family_id
    )
    # Seed an additional live sibling in the same family to prove family-sweep
    # reaches beyond just (A, B).
    _, hash_c = _seed_refresh(
        db_session, "carol@example.com", family_id=family_id
    )

    # 1st call: rotates A -> mints B, revokes A.
    r1 = client.post("/api/v1/auth/refresh", cookies={"refresh_token": raw_a})
    assert r1.status_code == 200, r1.text
    new_refresh = r1.json()["refresh_token"]
    hash_b = hashlib.sha256(new_refresh.encode("utf-8")).hexdigest()

    # 2nd call: replay A -> 401 auth_revoked AND every row in family F revoked.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        r2 = client.post(
            "/api/v1/auth/refresh", cookies={"refresh_token": raw_a}
        )
    assert r2.status_code == 401, r2.text
    body = r2.json()
    assert body == {"error": "auth_revoked"}
    # Do not leak the word "reuse" to the client.
    assert "reuse" not in r2.text.lower()

    # Every row in family F is revoked after the reuse sweep.
    db_session.expire_all()
    rows = db_session.exec(
        select(AuthSession).where(AuthSession.family_id == family_id)
    ).all()
    hashes = {r.refresh_token_hash for r in rows}
    assert {hash_a, hash_b, hash_c}.issubset(hashes)
    assert all(r.revoked_at is not None for r in rows)

    # WARN log emitted with the expected event + family_id.
    assert "event=refresh_family_reuse" in caplog.text
    assert family_id in caplog.text


def test_refresh_unknown_401(client: TestClient, db_session: Session) -> None:
    bogus = secrets.token_urlsafe(32)
    r = client.post("/api/v1/auth/refresh", cookies={"refresh_token": bogus})
    assert r.status_code == 401, r.text
    assert r.json() == {"error": "auth_unknown"}

    # DB remained clean (no rows minted, no rows touched).
    rows = db_session.exec(select(AuthSession)).all()
    assert rows == []
