"""S2 — first-class red-flag records + retention.expires_at (design 28547568 §6/§3).

Covers the ticket acceptance criteria:
  - event_flags rows written at ingest, one per (event, rule_id)
  - JSON flags on event + session stay in sync with the records
  - the six-kind taxonomy (category) + coarse severity are correct
  - retention_expires_at populated from policy (NULL by default, set when configured)
  - no new rule families; clean events write no records
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession
from sqlmodel import select

from apps.api.api.routers.receipt.models import (
    Event,
    EventFlag,
)
from apps.api.api.routers.receipt.models import (
    Session as SessionRow,
)
from apps.api.api.routers.receipt.red_flags import category_of, severity_of


def _event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "s-1", "user": "u-1", "kind": "tool_use",
        "tool": "Edit", "content": "noop",
    }
    base.update(overrides)
    return base


# ── taxonomy unit (the six kinds, no seventh) ────────────────────────────────

def test_category_of_covers_six_kinds():
    assert category_of("secret_aws") == "secret"
    assert category_of("secret_yoru_token") == "secret"
    assert category_of("shell_rm") == "shell"
    assert category_of("shell_git_force") == "shell"
    assert category_of("db_destructive") == "db"
    assert category_of("migration_file") == "migration"
    assert category_of("env_mutation") == "env"
    assert category_of("ci_config") == "ci"


def test_severity_tiers():
    assert severity_of("secret_aws") == "critical"
    assert severity_of("db_destructive") == "critical"
    assert severity_of("shell_rm") == "high"
    assert severity_of("ci_config") == "high"
    assert severity_of("env_mutation") == "medium"
    assert severity_of("migration_file") == "medium"


# ── ingest writes first-class records, in sync with the JSON arrays ──────────

def test_ingest_writes_event_flags(client: TestClient, engine, ingest_headers) -> None:
    # A .env file_change carrying an AWS secret trips TWO rules:
    # secret_aws (content) + env_mutation (path).
    ev = _event(
        session_id="s-flags", kind="file_change", tool="Edit", path=".env",
        content="AWS_SECRET=AKIAIOSFODNN7EXAMPLE",
        raw={"tool_name": "Edit", "tool_input": {
            "file_path": ".env", "new_string": "AWS_SECRET=AKIAIOSFODNN7EXAMPLE"}},
    )
    resp = client.post("/api/v1/sessions/events", json={"events": [ev]}, headers=ingest_headers)
    assert resp.status_code == 202, resp.text
    assert resp.json()["flagged_sessions"] == ["s-flags"]

    with DBSession(engine) as s:
        records = s.exec(
            select(EventFlag).where(EventFlag.session_id == "s-flags")
        ).all()
        by_rule = {r.rule_id: r for r in records}
        # one record per rule
        assert set(by_rule) == {"secret_aws", "env_mutation"}
        assert by_rule["secret_aws"].category == "secret"
        assert by_rule["secret_aws"].severity == "critical"
        assert by_rule["env_mutation"].category == "env"
        assert by_rule["env_mutation"].severity == "medium"
        # every record links to the concrete event + session
        event = s.exec(select(Event).where(Event.session_id == "s-flags")).first()
        for r in records:
            assert r.event_id == event.id
        # JSON arrays stay in sync (back-compat read path untouched)
        assert set(event.flags) == {"secret_aws", "env_mutation"}
        sess = s.get(SessionRow, "s-flags")
        assert set(sess.flags) == {"secret_aws", "env_mutation"}
        assert sess.flagged is True


def test_event_flags_carry_git_context(client: TestClient, engine, ingest_headers) -> None:
    """steal#6: a red-flag record freezes the parent session's git context
    (repo/branch/dir) so the risk log is self-explanatory without a join."""
    ev = _event(
        session_id="s-git", kind="file_change", tool="Edit", path=".env",
        content="AWS_SECRET=AKIAIOSFODNN7EXAMPLE",
        cwd="/home/dev/acme-app",
        git_remote="git@github.com:acme/app.git",
        git_branch="feature/x",
    )
    resp = client.post(
        "/api/v1/sessions/events", json={"events": [ev]}, headers=ingest_headers
    )
    assert resp.status_code == 202, resp.text
    with DBSession(engine) as s:
        recs = s.exec(
            select(EventFlag).where(EventFlag.session_id == "s-git")
        ).all()
        assert recs  # the .env AWS secret trips at least one rule
        for r in recs:
            assert r.cwd == "/home/dev/acme-app"
            assert r.git_remote == "git@github.com:acme/app.git"
            assert r.git_branch == "feature/x"


def test_clean_event_writes_no_records(client: TestClient, engine, ingest_headers) -> None:
    resp = client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-clean", kind="tool_use", tool="Bash", content="ls -la"),
    ]}, headers=ingest_headers)
    assert resp.status_code == 202
    with DBSession(engine) as s:
        assert s.exec(
            select(EventFlag).where(EventFlag.session_id == "s-clean")
        ).all() == []


# ── retention.expires_at populated from policy ───────────────────────────────

def test_retention_null_by_default(client: TestClient, engine, monkeypatch, ingest_headers) -> None:
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-ret0", content="hi"),
    ]}, headers=ingest_headers)
    with DBSession(engine) as s:
        sess = s.get(SessionRow, "s-ret0")
        ev = s.exec(select(Event).where(Event.session_id == "s-ret0")).first()
    assert sess.retention_expires_at is None
    assert ev.retention_expires_at is None


def test_retention_set_when_configured(client: TestClient, engine, monkeypatch, ingest_headers) -> None:
    monkeypatch.setenv("RETENTION_DAYS", "30")
    client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-ret30", content="hi"),
    ]}, headers=ingest_headers)
    with DBSession(engine) as s:
        sess = s.get(SessionRow, "s-ret30")
        ev = s.exec(select(Event).where(Event.session_id == "s-ret30")).first()
    assert sess.retention_expires_at is not None
    assert ev.retention_expires_at is not None
    # expiry ≈ event ts + 30 days
    assert abs((ev.retention_expires_at - ev.ts) - timedelta(days=30)) < timedelta(seconds=2)
