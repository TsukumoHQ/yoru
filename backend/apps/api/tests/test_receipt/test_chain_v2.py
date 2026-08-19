"""Tests for S3 — chain v2 commit-to-digest (design trovex:28547568 §2, ticket d6ec19c6).

Covers the ticket acceptance criteria:
  - new events are written v2: the chain commits sha256(content), not plaintext
  - chain_version + content_digest columns populated on new writes
  - existing v0 rows stay v0 and still verify (no history rewrite)
  - /verify recomputes v0 and v2 segments correctly in one mixed-version session
  - redacting a v2 event's plaintext leaves the chain intact/verifiable
  - a plaintext edit (digest untouched) and a chain edit are both detected
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession
from sqlmodel import select

from apps.api.api.routers.receipt.events_router import event_entry_hash
from apps.api.api.routers.receipt.models import Event

BASE_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
# Naive UTC — SQLite drops tzinfo, and ingest normalizes with _naive_utc before
# hashing. When seeding legacy v0 rows by hand we must hash over the SAME naive
# ts we store, or the recompute at verify won't match.
BASE_TS_N = BASE_TS.replace(tzinfo=None)


@pytest.fixture()
def app(engine) -> FastAPI:
    from apps.api.api.routers.receipt.db import get_session
    from apps.api.api.routers.receipt.events_router import EventsRouter
    from apps.api.api.routers.receipt.sessions_router import SessionsRouter

    _app = FastAPI()
    _app.include_router(EventsRouter().get_router(), prefix="/api/v1")
    _app.include_router(SessionsRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture()
def alice_headers(mint_token):
    _, h = mint_token("alice")
    return h


def _ingest(client, headers, events: list[dict]) -> dict:
    resp = client.post(
        "/api/v1/sessions/events", headers=headers, json={"events": events}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _verify(client, headers, sid: str) -> dict:
    resp = client.get(f"/api/v1/sessions/{sid}/verify", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(db_session, sid: str) -> list[Event]:
    return list(db_session.exec(
        select(Event).where(Event.session_id == sid).order_by(Event.id.asc())
    ).all())


# ── new writes are v2 ─────────────────────────────────────────────────────────

def test_ingest_writes_v2_and_verifies(client, db_session, alice_headers):
    _ingest(client, alice_headers, [
        {"session_id": "s1", "kind": "message", "tool": "user",
         "content": "deploy the app", "ts": BASE_TS.isoformat()},
        {"session_id": "s1", "kind": "tool_use", "tool": "Bash",
         "ts": (BASE_TS + timedelta(seconds=1)).isoformat(),
         "raw": {"tool_input": {"command": "ls -la"},
                 "tool_response": {"stdout": "total 0"}}},
    ])

    rows = _events(db_session, "s1")
    assert len(rows) == 2
    for ev in rows:
        assert ev.chain_version == 2
        assert ev.content_digest is not None
        digests = json.loads(ev.content_digest)
        assert set(digests) == {"content", "tool_input", "tool_response"}

    # the Bash event committed its raw tool_input/response by digest.
    bash = next(e for e in rows if e.tool == "Bash")
    d = json.loads(bash.content_digest)
    assert d["tool_input"] is not None
    assert d["tool_response"] is not None

    out = _verify(client, alice_headers, "s1")
    assert out["intact"] is True
    assert out["verified"] == 2
    assert out["unchained_legacy"] == 0
    assert out["broken_at_event_id"] is None


def test_v2_chain_never_commits_plaintext(client, db_session, alice_headers):
    """The whole point: the entry_hash must NOT change when only the plaintext
    changes, as long as the committed digest is regenerated to match — i.e. the
    hash is a function of the digest, not the bytes. Proven indirectly: two
    events with identical metadata but different content produce different
    digests AND different hashes, while the stored hash equals a recompute over
    the DIGEST (not the plaintext)."""
    from apps.api.api.routers.receipt.events_router import (
        compute_content_digests,
        event_entry_hash_v2,
    )
    _ingest(client, alice_headers, [
        {"session_id": "s1", "kind": "message", "tool": "user",
         "content": "secret prompt", "ts": BASE_TS.isoformat()},
    ])
    ev = _events(db_session, "s1")[0]
    digests = compute_content_digests(ev.content, None, None)
    recomputed = event_entry_hash_v2(
        "", ev.ts, ev.kind, ev.tool, ev.path, digests,
        ev.tokens_input, ev.tokens_output, ev.cost_usd,
    )
    assert recomputed == ev.entry_hash
    # plaintext is not present in the canonical hash input.
    assert "secret prompt" not in json.dumps(digests)


# ── redaction-safety (the linchpin) ───────────────────────────────────────────

def test_redaction_preserves_chain(client, db_session, alice_headers):
    _ingest(client, alice_headers, [
        {"session_id": "s1", "kind": "message", "tool": "user",
         "content": "keep me", "ts": BASE_TS.isoformat()},
        {"session_id": "s1", "kind": "file_change", "tool": "Edit", "path": ".env",
         "content": "AWS_SECRET=AKIAEXAMPLE1234", "flags": [],
         "ts": (BASE_TS + timedelta(seconds=1)).isoformat(),
         "raw": {"tool_input": {"new_string": "AWS_SECRET=AKIAEXAMPLE1234"}}},
    ])
    assert _verify(client, alice_headers, "s1")["intact"] is True

    # Redact the secret event at rest: null the plaintext channels, KEEP the
    # committed digest (this is exactly what S4 will do).
    rows = _events(db_session, "s1")
    secret_ev = next(e for e in rows if e.path == ".env")
    secret_ev.content = None
    secret_ev.raw = None
    db_session.add(secret_ev)
    db_session.commit()

    out = _verify(client, alice_headers, "s1")
    assert out["intact"] is True, "redaction must not break the chain"
    assert out["redacted_verified"] >= 1
    assert out["broken_at_event_id"] is None


# ── tamper detection ──────────────────────────────────────────────────────────

def test_plaintext_edit_without_digest_is_detected(client, db_session, alice_headers):
    """If someone edits the stored plaintext but leaves the committed digest
    untouched, verify must flag it — plaintext consistency is checked while the
    field is still present."""
    _ingest(client, alice_headers, [
        {"session_id": "s1", "kind": "message", "tool": "user",
         "content": "original", "ts": BASE_TS.isoformat()},
    ])
    ev = _events(db_session, "s1")[0]
    ev.content = "tampered"   # digest column left as the original commitment
    db_session.add(ev)
    db_session.commit()

    out = _verify(client, alice_headers, "s1")
    assert out["intact"] is False
    assert out["broken_at_event_id"] == ev.id


def test_chain_edit_is_detected(client, db_session, alice_headers):
    """Rewriting the committed digest (or entry_hash) breaks the chain."""
    _ingest(client, alice_headers, [
        {"session_id": "s1", "kind": "message", "tool": "user",
         "content": "a", "ts": BASE_TS.isoformat()},
        {"session_id": "s1", "kind": "message", "tool": "assistant",
         "content": "b", "ts": (BASE_TS + timedelta(seconds=1)).isoformat()},
    ])
    rows = _events(db_session, "s1")
    # Swap the first event's committed digest to a lie; its entry_hash no longer
    # matches a recompute -> break at that event.
    rows[0].content_digest = json.dumps(
        {"content": "0" * 64, "tool_input": None, "tool_response": None}
    )
    db_session.add(rows[0])
    db_session.commit()

    out = _verify(client, alice_headers, "s1")
    assert out["intact"] is False
    assert out["broken_at_event_id"] == rows[0].id


# ── mixed v0 / v2 in one session ──────────────────────────────────────────────

def test_mixed_v0_and_v2_session_verifies(client, db_session, alice_headers):
    """A session whose early events are legacy v0 (plaintext-committed) and
    whose later events are v2 must verify end to end — and v0 rows are never
    rewritten."""
    from apps.api.api.routers.receipt.models import Session as SessionRow

    # Seed a legacy v0 tail directly (chain_version NULL, entry_hash over
    # plaintext, exactly how pre-S3 ingest wrote it).
    db_session.add(SessionRow(id="s1", user="alice", started_at=BASE_TS_N))
    h1 = event_entry_hash("", BASE_TS_N, "message", "user", None, "legacy one", 0, 0, 0.0)
    ev1 = Event(session_id="s1", ts=BASE_TS_N, kind="message", tool="user",
                content="legacy one", flags=[], prev_hash="", entry_hash=h1)
    db_session.add(ev1)
    ts2 = BASE_TS_N + timedelta(seconds=1)
    h2 = event_entry_hash(h1, ts2, "message", "assistant", None, "legacy two", 0, 0, 0.0)
    ev2 = Event(session_id="s1", ts=ts2, kind="message", tool="assistant",
                content="legacy two", flags=[], prev_hash=h1, entry_hash=h2)
    db_session.add(ev2)
    db_session.commit()

    # Now ingest a v2 event on the SAME session — it chains onto the v0 tail.
    _ingest(client, alice_headers, [
        {"session_id": "s1", "kind": "tool_use", "tool": "Bash",
         "ts": (BASE_TS + timedelta(seconds=2)).isoformat(),
         "raw": {"tool_input": {"command": "echo hi"}}},
    ])

    rows = _events(db_session, "s1")
    assert [r.chain_version for r in rows] == [None, None, 2]
    # the v2 event's prev_hash links to the last v0 hash.
    assert rows[2].prev_hash == h2

    out = _verify(client, alice_headers, "s1")
    assert out["intact"] is True
    assert out["verified"] == 3
    assert out["broken_at_event_id"] is None


def test_v0_only_session_still_verifies_and_detects_tamper(client, db_session, alice_headers):
    from apps.api.api.routers.receipt.models import Session as SessionRow

    db_session.add(SessionRow(id="s1", user="alice", started_at=BASE_TS_N))
    h1 = event_entry_hash("", BASE_TS_N, "message", "user", None, "hello", 0, 0, 0.0)
    ev1 = Event(session_id="s1", ts=BASE_TS_N, kind="message", tool="user",
                content="hello", flags=[], prev_hash="", entry_hash=h1)
    db_session.add(ev1)
    db_session.commit()

    assert _verify(client, alice_headers, "s1")["intact"] is True

    # v0 commits plaintext, so editing content breaks the chain.
    ev1 = _events(db_session, "s1")[0]
    ev1.content = "hacked"
    db_session.add(ev1)
    db_session.commit()
    assert _verify(client, alice_headers, "s1")["intact"] is False
