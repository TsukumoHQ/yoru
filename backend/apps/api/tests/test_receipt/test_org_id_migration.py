"""M1 (multi-tenant, design 44a3774a §2): org_id columns + legacy backfill.

Drives receipt_db.init_db() on an isolated engine and asserts the additive
migration: org_id lands on sessions/events/event_flags with an index each, and
_run_orgid_backfill_once assigns the default org to legacy un-scoped rows
exactly once.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as DBSession
from sqlmodel import create_engine

from apps.api.api.routers.receipt import db as receipt_db
from apps.api.api.routers.receipt.models import (
    DEFAULT_ORG_ID,
    Event,
    EventFlag,
    Session,
)


@pytest.fixture()
def fresh_engine(monkeypatch):
    """A fresh in-memory engine bound as the receipt engine, migrated via init_db()."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    monkeypatch.setattr(receipt_db, "engine", eng)
    receipt_db.init_db()
    return eng


def _cols(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}


def _indexes(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(text(f"PRAGMA index_list({table})")).fetchall()}


def test_org_id_column_present_on_all_audit_tables(fresh_engine):
    with fresh_engine.begin() as conn:
        for tbl in ("sessions", "events", "event_flags"):
            assert "org_id" in _cols(conn, tbl), f"{tbl} missing org_id"
            assert f"ix_{tbl}_org_id" in _indexes(conn, tbl), f"{tbl} missing org_id index"


def test_sessions_org_id_index_survives_the_drop_index_cleanup(fresh_engine):
    # db.py unconditionally runs `DROP INDEX IF EXISTS ix_sessions_org_id` (legacy
    # org_id→workspace_id cleanup). A second init_db must leave the index intact.
    receipt_db.init_db()
    with fresh_engine.begin() as conn:
        assert "ix_sessions_org_id" in _indexes(conn, "sessions")


def test_backfill_assigns_default_org_to_legacy_rows(fresh_engine):
    # Simulate legacy un-scoped rows (org_id NULL) + reset the done-marker so the
    # one-shot backfill runs again.
    with DBSession(fresh_engine) as s:
        s.add(Session(id="s-legacy", user="a@x.dev", org_id=None))
        s.add(Event(session_id="s-legacy", kind="tool_use", org_id=None))
        s.add(EventFlag(
            session_id="s-legacy", rule_id="r1", category="shell",
            severity="high", org_id=None,
        ))
        s.commit()
    with fresh_engine.begin() as conn:
        conn.execute(text("DELETE FROM _schema_markers WHERE key = '_orgid_backfill_done'"))

    with fresh_engine.begin() as conn:
        receipt_db._run_orgid_backfill_once(conn)

    with fresh_engine.begin() as conn:
        for tbl in ("sessions", "events", "event_flags"):
            nulls = conn.execute(
                text(f"SELECT COUNT(*) FROM {tbl} WHERE org_id IS NULL")
            ).first()[0]
            assert nulls == 0, f"{tbl} still has un-scoped rows"
            got = conn.execute(text(f"SELECT org_id FROM {tbl}")).first()[0]
            assert got == DEFAULT_ORG_ID


def test_backfill_is_one_shot_new_null_rows_not_touched(fresh_engine):
    # After the marker is set, a subsequently-inserted NULL row must NOT be
    # backfilled — new writes are responsible for stamping org_id (M2), the
    # backfill only ever heals pre-M1 legacy rows.
    with fresh_engine.begin() as conn:
        receipt_db._run_orgid_backfill_once(conn)  # sets the marker (no legacy rows)
    with DBSession(fresh_engine) as s:
        s.add(Session(id="s-new", user="b@x.dev", org_id=None))
        s.commit()
    with fresh_engine.begin() as conn:
        receipt_db._run_orgid_backfill_once(conn)  # marker set → no-op
    with fresh_engine.begin() as conn:
        got = conn.execute(
            text("SELECT org_id FROM sessions WHERE id = 's-new'")
        ).first()[0]
        assert got is None
