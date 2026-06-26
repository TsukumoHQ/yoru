"""Tests for issue #79 — public session share opt-in.

Covers:
- POST /sessions/{id}/share (authed, owner-only, idempotent)
- POST /sessions/{id}/share/revoke (authed, owner-only, idempotent)
- GET /api/v1/public/sessions/{id} (unauth, 404 when private, redaction)

Redaction rules under test:
- `user` / `cwd` / `git_remote` / `git_branch` / `workspace_id` are NOT
  in the public payload (PII + infra-leak surfaces).
- Events tagged `secret_*` have `content`, `output`, `tool_input` stripped.
  Non-secret flagged events (shell_rm, db_destructive) stay verbatim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.models import Event, Session as SessionRow


BASE_TS = datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def app(engine) -> FastAPI:
    """Mount both the authed SessionsRouter (for /share) and the unauth
    PublicSessionsRouter (for /public/sessions/{id})."""
    from apps.api.api.routers.receipt.db import get_session
    from apps.api.api.routers.receipt.public_sessions_router import (
        PublicSessionsRouter,
    )
    from apps.api.api.routers.receipt.sessions_router import SessionsRouter

    _app = FastAPI()
    _app.include_router(SessionsRouter().get_router(), prefix="/api/v1")
    _app.include_router(PublicSessionsRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture()
def alice_headers(mint_token):
    _, h = mint_token("alice")
    return h


@pytest.fixture()
def bob_headers(mint_token):
    _, h = mint_token("bob")
    return h


def _seed_session(
    db_session, *, sid: str = "s1", user: str = "alice", is_public: bool = False
) -> None:
    db_session.add(
        SessionRow(
            id=sid,
            user=user,
            started_at=BASE_TS,
            cost_usd=0.10,
            flagged=False,
            flags=[],
            is_public=is_public,
            cwd="/Users/alice/work/acme",
            git_remote="git@github.com:acme/private.git",
            git_branch="main",
        )
    )
    db_session.commit()


def _seed_events(db_session, *, sid: str = "s1") -> None:
    """Two events: one clean tool_use, one secret-flagged file_change."""
    db_session.add_all(
        [
            Event(
                session_id=sid,
                ts=BASE_TS + timedelta(seconds=1),
                kind="tool_use",
                tool="Bash",
                content="ls -la",
                flags=[],
                raw={"tool_name": "Bash", "tool_input": {"command": "ls -la"}},
            ),
            Event(
                session_id=sid,
                ts=BASE_TS + timedelta(seconds=2),
                kind="file_change",
                tool="Edit",
                path=".env",
                content="AWS_SECRET=AKIAEXAMPLE1234ABCDE",
                flags=["secret_aws"],
                raw={
                    "tool_name": "Edit",
                    "tool_input": {
                        "file_path": ".env",
                        "new_string": "AWS_SECRET=AKIAEXAMPLE1234ABCDE",
                    },
                },
            ),
        ]
    )
    db_session.commit()


# ---------- POST /sessions/{id}/share ----------

def test_share_flips_session_public(client, db_session, alice_headers):
    _seed_session(db_session, sid="s1", user="alice", is_public=False)
    resp = client.post("/api/v1/sessions/s1/share", headers=alice_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_public"] is True
    assert body["session_id"] == "s1"
    assert body["public_url"].endswith("/s/s1")

    # DB state flipped.
    db_session.expire_all()
    row = db_session.get(SessionRow, "s1")
    assert row.is_public is True


def test_share_is_idempotent(client, db_session, alice_headers):
    _seed_session(db_session, sid="s1", user="alice", is_public=True)
    resp = client.post("/api/v1/sessions/s1/share", headers=alice_headers)
    assert resp.status_code == 200
    assert resp.json()["is_public"] is True


def test_share_cross_user_404(client, db_session, bob_headers):
    # alice owns s1 — bob cannot flip it public.
    _seed_session(db_session, sid="s1", user="alice")
    resp = client.post("/api/v1/sessions/s1/share", headers=bob_headers)
    assert resp.status_code == 404
    # DB still private — cross-user POST didn't side-effect.
    row = db_session.get(SessionRow, "s1")
    assert row.is_public is False


def test_share_unauth_401(client, db_session):
    _seed_session(db_session, sid="s1", user="alice")
    resp = client.post("/api/v1/sessions/s1/share")
    assert resp.status_code == 401


def test_share_unknown_session_404(client, alice_headers):
    resp = client.post("/api/v1/sessions/nope/share", headers=alice_headers)
    assert resp.status_code == 404


# ---------- POST /sessions/{id}/share/revoke ----------

def test_revoke_flips_session_private(client, db_session, alice_headers):
    _seed_session(db_session, sid="s1", user="alice", is_public=True)
    resp = client.post(
        "/api/v1/sessions/s1/share/revoke", headers=alice_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_public"] is False
    assert body["public_url"] is None
    row = db_session.get(SessionRow, "s1")
    assert row.is_public is False


def test_revoke_idempotent_on_already_private(
    client, db_session, alice_headers
):
    _seed_session(db_session, sid="s1", user="alice", is_public=False)
    resp = client.post(
        "/api/v1/sessions/s1/share/revoke", headers=alice_headers
    )
    assert resp.status_code == 200
    assert resp.json()["is_public"] is False


def test_revoke_cross_user_404(client, db_session, bob_headers):
    _seed_session(db_session, sid="s1", user="alice", is_public=True)
    resp = client.post("/api/v1/sessions/s1/share/revoke", headers=bob_headers)
    assert resp.status_code == 404


# ---------- GET /api/v1/public/sessions/{id} ----------

def test_public_get_private_returns_404(client, db_session):
    _seed_session(db_session, sid="s1", user="alice", is_public=False)
    resp = client.get("/api/v1/public/sessions/s1")
    assert resp.status_code == 404
    # Same 404 shape as "unknown id" — callers can't distinguish the two.


def test_public_get_unknown_returns_404(client):
    resp = client.get("/api/v1/public/sessions/nope")
    assert resp.status_code == 404


def test_public_get_public_session_returns_200(client, db_session):
    _seed_session(db_session, sid="s1", user="alice", is_public=True)
    _seed_events(db_session, sid="s1")
    resp = client.get("/api/v1/public/sessions/s1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "s1"
    # Score present (computed from events).
    assert body["score"] is not None


def test_public_get_omits_pii_fields(client, db_session):
    _seed_session(db_session, sid="s1", user="alice@corp.com", is_public=True)
    _seed_events(db_session, sid="s1")
    resp = client.get("/api/v1/public/sessions/s1")
    body = resp.json()
    # Explicit allow-list — enumerate the fields that MUST NOT appear.
    for pii_field in ("user", "cwd", "git_remote", "git_branch", "workspace_id"):
        assert pii_field not in body, (
            f"PII field {pii_field!r} leaked in public response: {body.keys()}"
        )


def test_public_get_redacts_secret_flagged_event(client, db_session):
    _seed_session(db_session, sid="s1", user="alice", is_public=True)
    _seed_events(db_session, sid="s1")
    resp = client.get("/api/v1/public/sessions/s1")
    body = resp.json()
    events = body["events"]
    # First event is clean Bash — content stays visible.
    bash_event = next(e for e in events if e["tool"] == "Bash")
    assert bash_event["content"] == "ls -la"
    # Second event has secret_aws flag — content/output/tool_input all redacted.
    edit_event = next(e for e in events if e["tool"] == "Edit")
    assert "secret_aws" in edit_event["flags"]
    assert edit_event["content"] is None
    assert edit_event["output"] is None
    assert edit_event["tool_input"] is None
    # Structural fields stay — viewers still see "Claude tried to edit .env".
    assert edit_event["path"] == ".env"
    assert edit_event["kind"] == "file_change"


def test_public_get_keeps_non_secret_flags_verbatim(client, db_session):
    """shell_rm, db_destructive, etc. are visible with full content — the
    narrative ('Claude tried to rm -rf node_modules') is the whole point."""
    _seed_session(db_session, sid="s1", user="alice", is_public=True)
    db_session.add(
        Event(
            session_id="s1",
            ts=BASE_TS + timedelta(seconds=1),
            kind="tool_use",
            tool="Bash",
            content="rm -rf node_modules",
            flags=["shell_rm"],
            raw={
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf node_modules"},
            },
        )
    )
    db_session.commit()
    resp = client.get("/api/v1/public/sessions/s1")
    body = resp.json()
    bash = body["events"][0]
    assert bash["flags"] == ["shell_rm"]
    assert bash["content"] == "rm -rf node_modules"  # NOT redacted


# ---------- TSU-44 redaction-pass: scrub UNFLAGGED secrets/paths/repos ----------

def test_public_scrubs_unflagged_secrets_paths_and_repos(client, db_session):
    """The flag-based strip only nulls events the scanner flagged secret_*.
    The redaction-pass must additionally mask credentials, absolute home
    paths, and git-remote URLs that slipped through UNFLAGGED — in event
    content/path/tool_input AND the session title/summary/file list."""
    db_session.add(
        SessionRow(
            id="redact1",
            user="bob",
            started_at=BASE_TS,
            cost_usd=0.10,
            flagged=False,
            flags=[],
            is_public=True,
            title="fix /Users/bob/secret-proj/app.py leaking AKIAIOSFODNN7EXAMPLE",
            summary="cloned git@github.com:acme/private-repo.git into /home/bob/work",
        )
    )
    db_session.add(
        Event(
            session_id="redact1",
            ts=BASE_TS + timedelta(seconds=1),
            kind="file_change",
            tool="Edit",
            path="/Users/bob/secret-proj/.env.local",
            content="STRIPE=sk_test_abcdefghijklmnopqrstuvwx0123",
            flags=[],  # NOT flagged — the gap the scrubber closes
            raw={
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/Users/bob/secret-proj/.env.local",
                    "repo": "https://github.com/acme/secret-thing",
                },
            },
        )
    )
    db_session.commit()

    body = client.get("/api/v1/public/sessions/redact1").json()
    blob = __import__("json").dumps(body)

    # No raw secret / absolute home path / repo identity anywhere in the wire.
    assert "AKIAIOSFODNN7EXAMPLE" not in blob
    assert "sk_test_abcdefghijklmnopqrstuvwx0123" not in blob
    assert "/Users/bob" not in blob
    assert "/home/bob" not in blob
    assert "acme/private-repo" not in blob
    assert "acme/secret-thing" not in blob
    # Masks present (proves the scrubber ran, not that the field was dropped).
    assert "[redacted:" in blob or "~" in blob
    ev = body["events"][0]
    assert ev["content"] == "STRIPE=[redacted:stripe]"
    assert ev["path"] == "~/secret-proj/.env.local"
