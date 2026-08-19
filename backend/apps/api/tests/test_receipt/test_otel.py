"""Tests for S1 — OTel-GenAI projection (design trovex:28547568, ticket d6ec19c6).

Covers the ticket acceptance criteria:
  - Session->invoke_agent, tool->execute_tool, token->chat, message->content-event
  - content lands in span EVENTS, not attributes (redactable channel)
  - format=otlp on export + GET /sessions/{id}/otlp return valid OTLP/JSON
  - public-path projection strips enduser.id/git.*/cwd/workspace.id (no new leak)
  - GET .../otlp requires auth on the owner surface
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.models import Event, Session as SessionRow

BASE_TS = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

SECRET_LITERAL = "AKIAEXAMPLE1234ABCDE"


@pytest.fixture()
def app(engine) -> FastAPI:
    from apps.api.api.routers.receipt.db import get_session
    from apps.api.api.routers.receipt.export_router import ExportRouter
    from apps.api.api.routers.receipt.public_sessions_router import (
        PublicSessionsRouter,
    )
    from apps.api.api.routers.receipt.sessions_router import SessionsRouter

    _app = FastAPI()
    # ExportRouter BEFORE SessionsRouter — same order as main.py, so
    # /sessions/export isn't shadowed by /sessions/{session_id}.
    _app.include_router(ExportRouter().get_router(), prefix="/api/v1")
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


def _seed(db_session, *, sid="s1", user="alice", is_public=False) -> None:
    db_session.add(SessionRow(
        id=sid, user=user, agent="claude-code",
        started_at=BASE_TS, ended_at=BASE_TS + timedelta(seconds=30),
        tools_count=2, files_count=1, tokens_input=100, tokens_output=50,
        cost_usd=0.01, flagged=True, flags=["secret_aws"],
        files_changed=[".env"], tools_called=["Bash", "Edit"],
        is_public=is_public,
        cwd="/Users/alice/work/acme",
        git_remote="git@github.com:acme/private.git",
        git_branch="main",
        workspace_id="ws-acme",
    ))
    db_session.add_all([
        Event(
            session_id=sid, ts=BASE_TS + timedelta(seconds=1),
            kind="message", tool="user", content="deploy the app", flags=[],
        ),
        Event(
            session_id=sid, ts=BASE_TS + timedelta(seconds=2),
            kind="tool_use", tool="Bash", content="ls -la", flags=[],
            entry_hash="hash2", prev_hash="",
            raw={"tool_name": "Bash", "tool_input": {"command": "ls -la"},
                 "tool_response": {"stdout": "total 0"}},
        ),
        Event(
            session_id=sid, ts=BASE_TS + timedelta(seconds=3),
            kind="file_change", tool="Edit", path=".env",
            content=f"AWS_SECRET={SECRET_LITERAL}", flags=["secret_aws"],
            entry_hash="hash3", prev_hash="hash2",
            raw={"tool_name": "Edit", "tool_input": {
                "file_path": ".env", "new_string": f"AWS_SECRET={SECRET_LITERAL}"}},
        ),
        Event(
            session_id=sid, ts=BASE_TS + timedelta(seconds=4),
            kind="token", tool="model", tokens_input=100, tokens_output=50,
            cost_usd=0.01, flags=[],
            raw={"model": "claude-sonnet-4-5",
                 "usage": {"input_tokens": 100, "output_tokens": 50},
                 "stop_reason": "end_turn"},
        ),
        Event(
            session_id=sid, ts=BASE_TS + timedelta(seconds=5),
            kind="message", tool="assistant", content="done", flags=[],
        ),
    ])
    db_session.commit()


# ── helpers to introspect an OTLP trace ──────────────────────────────────────

def _spans(trace: dict) -> list[dict]:
    return trace["resourceSpans"][0]["scopeSpans"][0]["spans"]


def _by_op(spans: list[dict]) -> dict:
    """Map gen_ai.operation.name -> list of spans."""
    out: dict[str, list[dict]] = {}
    for sp in spans:
        op = _attr(sp, "gen_ai.operation.name")
        out.setdefault(op, []).append(sp)
    return out


def _attr(span: dict, key: str):
    """Return the scalar inside the first matching KeyValue's AnyValue."""
    for kv in span.get("attributes", []):
        if kv["key"] == key:
            return next(iter(kv["value"].values()))
    return None


def _attr_keys(span: dict) -> set:
    return {kv["key"] for kv in span.get("attributes", [])}


def _all_text(obj) -> str:
    """Flatten a whole trace to one string for leak assertions."""
    return json.dumps(obj)


# ── owner path ───────────────────────────────────────────────────────────────

def test_owner_otlp_shape(client, db_session, alice_headers):
    _seed(db_session)
    resp = client.get("/api/v1/sessions/s1/otlp", headers=alice_headers)
    assert resp.status_code == 200
    trace = resp.json()

    spans = _spans(trace)
    ops = _by_op(spans)
    # invoke_agent root + execute_tool (x2) + chat (x1)
    assert len(ops["invoke_agent"]) == 1
    assert len(ops["execute_tool"]) == 2
    assert len(ops["chat"]) == 1

    root = ops["invoke_agent"][0]
    assert _attr(root, "gen_ai.conversation.id") == "s1"
    assert _attr(root, "gen_ai.agent.name") == "claude-code"
    assert _attr(root, "gen_ai.provider.name") == "anthropic"
    assert _attr(root, "enduser.id") == "alice"  # owner path keeps identity
    # messages became content EVENTS on the root, not spans.
    root_event_names = {e["name"] for e in root.get("events", [])}
    assert "gen_ai.user.message" in root_event_names
    assert "gen_ai.assistant.message" in root_event_names

    # chat span carries usage token counts under the modern OTel names.
    chat = ops["chat"][0]
    assert _attr(chat, "gen_ai.usage.input_tokens") == "100"
    assert _attr(chat, "gen_ai.usage.output_tokens") == "50"
    assert _attr(chat, "gen_ai.request.model") == "claude-sonnet-4-5"

    # parent linkage: every child points at the root span.
    root_sid = root["spanId"]
    for sp in ops["execute_tool"] + ops["chat"]:
        assert sp["parentSpanId"] == root_sid


def test_content_in_events_not_attributes(client, db_session, alice_headers):
    """The redactability rule: tool call arguments/result live in span EVENTS,
    never as span attributes."""
    _seed(db_session)
    trace = client.get("/api/v1/sessions/s1/otlp", headers=alice_headers).json()
    tool_spans = _by_op(_spans(trace))["execute_tool"]
    bash = next(s for s in tool_spans if _attr(s, "gen_ai.tool.name") == "Bash")

    # arguments/result are NOT attributes on the span.
    assert "gen_ai.tool.call.arguments" not in _attr_keys(bash)
    assert "gen_ai.tool.call.result" not in _attr_keys(bash)
    # they ARE in a span event body.
    ev = next(e for e in bash["events"] if e["name"] == "gen_ai.tool.message")
    ev_keys = {kv["key"] for kv in ev["attributes"]}
    assert "gen_ai.tool.call.arguments" in ev_keys
    assert "gen_ai.tool.call.result" in ev_keys
    # yoru compliance overlay rides the attributes.
    assert _attr(bash, "yoru.event.hash") == "hash2"


def test_owner_export_otlp_format(client, db_session, alice_headers):
    _seed(db_session, sid="s1")
    _seed(db_session, sid="s2")
    resp = client.get(
        "/api/v1/sessions/export?format=otlp", headers=alice_headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["content-disposition"].endswith('.otlp.jsonl"')
    lines = [ln for ln in resp.text.split("\n") if ln]
    assert len(lines) == 2  # one OTLP trace per session
    for ln in lines:
        trace = json.loads(ln)
        assert _by_op(_spans(trace))["invoke_agent"]  # valid trace each line


def test_owner_otlp_requires_auth(client, db_session):
    _seed(db_session)
    assert client.get("/api/v1/sessions/s1/otlp").status_code == 401
    assert client.get("/api/v1/sessions/export?format=otlp").status_code == 401


# ── public path — the no-new-leak guarantee ──────────────────────────────────

def test_public_otlp_redacts_pii_and_secrets(client, db_session):
    _seed(db_session, sid="pub1", is_public=True)
    resp = client.get("/api/v1/public/sessions/pub1/otlp")
    assert resp.status_code == 200
    trace = resp.json()
    blob = _all_text(trace)

    # invoke_agent root still present (it's a valid trace)…
    assert _by_op(_spans(trace))["invoke_agent"]
    # …but the PII / infra-leak fields never appear anywhere in the trace.
    assert "enduser.id" not in blob
    assert "alice" not in blob
    assert "git@github.com" not in blob
    assert "yoru.git.remote" not in blob
    assert "/Users/alice" not in blob
    assert "ws-acme" not in blob
    # the flagged secret's raw value is stripped by the public redaction.
    assert SECRET_LITERAL not in blob


def test_public_otlp_404_when_private(client, db_session):
    _seed(db_session, sid="priv1", is_public=False)
    assert client.get("/api/v1/public/sessions/priv1/otlp").status_code == 404


def test_public_otlp_404_when_absent(client):
    assert client.get("/api/v1/public/sessions/nope/otlp").status_code == 404
