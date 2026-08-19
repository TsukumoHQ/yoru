"""Data-retention pruning.

A self-hosted instance accumulates sessions/events in SQLite forever. Retention
lets the operator cap that — and, for regulated users (e.g. legal), enforce a
records-retention policy. ``RETENTION_DAYS=0`` keeps everything.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text

from apps.api.api.routers.receipt.db import engine


def retention_days() -> int:
    """Configured retention window in days. ``RETENTION_DAYS=0`` (default) means
    keep forever. S2 records an expiry from this; S5 will add the >=6mo
    compliance floor + enforcement on top of the same knob."""
    try:
        return int(os.environ.get("RETENTION_DAYS", "0"))
    except ValueError:
        return 0


def retention_expires_at(ts: datetime, days: Optional[int] = None) -> Optional[datetime]:
    """Naive-UTC instant at which an event/session recorded at ``ts`` becomes
    retention-eligible, or None when retention is disabled (keep forever).

    Pure + policy-driven so ingest can stamp ``retention_expires_at`` per record
    without any scheduler. ``ts`` is stored naive-UTC; the return matches.
    """
    d = retention_days() if days is None else days
    if d <= 0:
        return None
    base = ts if ts.tzinfo is None else ts.astimezone(timezone.utc).replace(tzinfo=None)
    return base + timedelta(days=d)


def prune_older_than(days: int) -> dict:
    """Delete sessions (and their events) whose start is older than ``days``.

    Returns the number of pruned sessions/events. A no-op for days <= 0.
    """
    if days <= 0:
        return {"pruned_sessions": 0, "pruned_events": 0}
    # SQLite stores naive UTC; compare against a naive cutoff.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    cutoff_iso = cutoff.isoformat()

    with engine.begin() as conn:
        sess_ids = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM sessions WHERE started_at < :c"),
                {"c": cutoff_iso},
            ).fetchall()
        ]
        if not sess_ids:
            return {"pruned_sessions": 0, "pruned_events": 0, "cutoff": cutoff_iso}

        ev_count = 0
        # Chunk the IN clause to stay well under SQLite's variable limit.
        for i in range(0, len(sess_ids), 400):
            chunk = sess_ids[i : i + 400]
            marks = ",".join(f":id{j}" for j in range(len(chunk)))
            params = {f"id{j}": sid for j, sid in enumerate(chunk)}
            ev_count += conn.execute(
                text(f"DELETE FROM events WHERE session_id IN ({marks})"), params
            ).rowcount or 0
            conn.execute(
                text(f"DELETE FROM sessions WHERE id IN ({marks})"), params
            )

    return {
        "pruned_sessions": len(sess_ids),
        "pruned_events": ev_count,
        "cutoff": cutoff_iso,
    }
