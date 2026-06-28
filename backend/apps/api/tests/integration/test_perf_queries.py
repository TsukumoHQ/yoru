"""Wave-15 C3 — N+1 audit for /api/v1/sessions and /api/v1/dashboard/team.

Audit result: clean, no N+1.

- `SessionsRouter.list_sessions` issues exactly two statements: the bounded
  `select(SessionRow)...limit(limit)` and a sibling
  `select(func.count()).select_from(SessionRow)` sharing the same filter list.
  Nothing iterates per-row; `Event` is never touched.
- `DashboardRouter.team` emits one aggregated `select(user, count, sum,
  sum).group_by(user)` statement; the Python post-processing loops over the
  already-materialized aggregate rows (N = distinct users, not sessions).

These tests lock that shape in: adding rows (or events) must not add queries.
The auth dependency `require_current_user` would itself issue 1 SELECT + 1
UPDATE on hook_tokens, which is orthogonal to router N+1 behavior — it is
overridden with a static user so the query counter sees router statements
only (matches the ≤3 / ≤5 thresholds specified in the brief).
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

# Point the receipt package at in-memory sqlite BEFORE it gets imported.
os.environ.setdefault("RECEIPT_DB_URL", "sqlite:///:memory:")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session as SQLSession  # noqa: E402
from sqlmodel import SQLModel, create_engine

from apps.api.api.dependencies.auth import (  # noqa: E402
    get_current_user_id,
    get_current_user_token,
)
from apps.api.api.routers.receipt.dashboard_router import DashboardRouter  # noqa: E402
from apps.api.api.routers.receipt.db import get_session  # noqa: E402
from apps.api.api.routers.receipt.deps import require_current_user  # noqa: E402
from apps.api.api.routers.receipt.models import Event  # noqa: E402
from apps.api.api.routers.receipt.models import Session as SessionRow
from apps.api.api.routers.receipt.sessions_router import SessionsRouter  # noqa: E402

PERF_USER = "perf-user@test.local"
PERF_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


@pytest.fixture
def clean_db():
    """Override integration/conftest.py autouse — this file uses isolated in-memory engines."""
    yield


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def app(engine) -> FastAPI:
    _app = FastAPI()
    _app.include_router(SessionsRouter().get_router(), prefix="/api/v1")
    _app.include_router(DashboardRouter().get_router(), prefix="/api/v1")

    def _override_session():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override_session
    _app.dependency_overrides[require_current_user] = lambda: PERF_USER
    # DashboardRouter switched its auth deps to get_current_user_id /
    # get_current_user_token (it no longer uses require_current_user), so the
    # override above doesn't reach /dashboard/* — override those too.
    _app.dependency_overrides[get_current_user_id] = lambda: PERF_USER_ID
    _app.dependency_overrides[get_current_user_token] = lambda: "perf-token"
    return _app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_visibility(monkeypatch):
    """The sessions list handler lazily imports visible_emails_sync, which
    resolves the caller's group co-members from the GLOBAL data store singleton.
    That singleton leaks across the full suite (other tests repoint it at an
    in-memory engine missing `datastore_records`), so stub it to the perf user's
    own scope — this also keeps the N+1 query count deterministic.
    """
    monkeypatch.setattr(
        "apps.api.api.services.access.visibility.visible_emails_sync",
        lambda caller_email: {PERF_USER},
    )


class _QueryCounter:
    """Count SQL executions via a `before_cursor_execute` listener.

    The Engine-class-level listener fires for every engine in the process;
    since the `engine` fixture is the only engine present during the test,
    all captured statements belong to the router under test.
    """

    def __init__(self) -> None:
        self.count = 0
        self.statements: list[str] = []

    def __enter__(self):
        event.listen(Engine, "before_cursor_execute", self._on_exec)
        return self

    def __exit__(self, *exc):
        event.remove(Engine, "before_cursor_execute", self._on_exec)

    def _on_exec(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1
        self.statements.append(statement)


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def _seed_sessions_with_events(
    engine, n_sessions: int, events_per_session: int, user: str = PERF_USER
) -> None:
    """Bulk-seed sessions + events in one transaction (seeding cost excluded)."""
    base = _naive_utc(datetime.now(UTC))
    with SQLSession(engine) as db:
        for i in range(n_sessions):
            sess = SessionRow(
                id=f"s{i:04d}",
                user=user,
                started_at=base - timedelta(minutes=i),
                cost_usd=0.01 * i,
                flagged=(i % 4 == 0),
            )
            db.add(sess)
            for j in range(events_per_session):
                db.add(
                    Event(
                        session_id=sess.id,
                        ts=base - timedelta(minutes=i, seconds=j),
                        kind="tool_use",
                        tool="Bash",
                        content=f"echo {i}-{j}",
                    )
                )
        db.commit()


PERF_WORKSPACE_ID = "ws-perf-team"


def _seed_dashboard(engine, n_users: int, sessions_per_user: int) -> None:
    base = _naive_utc(datetime.now(UTC))
    with SQLSession(engine) as db:
        for u in range(n_users):
            email = f"user{u}@test.local"
            for i in range(sessions_per_user):
                db.add(
                    SessionRow(
                        id=f"u{u}-s{i:03d}",
                        user=email,
                        # Team dashboard scopes by the caller's workspace
                        # membership; put the fixture team in one workspace.
                        workspace_id=PERF_WORKSPACE_ID,
                        started_at=base - timedelta(minutes=i),
                        cost_usd=0.10 * i,
                        flagged=(i % 3 == 0),
                    )
                )
        db.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sessions_list_query_count(engine, client: TestClient) -> None:
    """GET /api/v1/sessions against 20 sessions × 5 events ≤ 3 queries."""
    _seed_sessions_with_events(engine, n_sessions=20, events_per_session=5)

    with _QueryCounter() as counter:
        resp = client.get("/api/v1/sessions")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 20
    assert len(body["items"]) == 20
    assert counter.count <= 3, (
        f"N+1 regression: {counter.count} queries for list of 20 sessions.\n"
        + "\n".join(counter.statements)
    )


def test_dashboard_team_query_count(
    engine, client: TestClient, monkeypatch
) -> None:
    """GET /api/v1/dashboard/team against 5 users × 10 sessions ≤ 5 queries."""
    _seed_dashboard(engine, n_users=5, sessions_per_user=10)

    # _resolve_scope reads workspace membership from the Supabase service-role
    # client, which isn't available in-process. Stub it so the perf caller is
    # scoped to the seeded team workspace.
    monkeypatch.setattr(
        "apps.api.api.routers.receipt.dashboard_router._resolve_scope",
        lambda user_id: ([PERF_WORKSPACE_ID], PERF_USER),
    )

    with _QueryCounter() as counter:
        resp = client.get("/api/v1/dashboard/team")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["sessions"] == 50
    assert len(body["users"]) == 5
    assert counter.count <= 5, (
        f"N+1 regression: {counter.count} queries for team dashboard of 5 users.\n"
        + "\n".join(counter.statements)
    )


def test_sessions_list_query_count_scales_constant(
    engine, client: TestClient
) -> None:
    """Query count for GET /api/v1/sessions is O(1) — identical at 10 and 50 rows."""
    counts: dict[int, int] = {}
    for n in (10, 50):
        # Reset the table between runs so the two sizes don't contaminate each other.
        with SQLSession(engine) as db:
            db.exec(Event.__table__.delete())  # type: ignore[attr-defined]
            db.exec(SessionRow.__table__.delete())  # type: ignore[attr-defined]
            db.commit()

        _seed_sessions_with_events(engine, n_sessions=n, events_per_session=3)

        with _QueryCounter() as counter:
            resp = client.get(f"/api/v1/sessions?limit={n}")
            assert resp.status_code == 200, resp.text
            assert resp.json()["total"] == n
        counts[n] = counter.count

    assert counts[10] == counts[50], (
        f"Query count scales with row count (O(n) smell): "
        f"10→{counts[10]}, 50→{counts[50]}. Suspected N+1."
    )
    assert counts[10] <= 3, f"Baseline above ≤3 threshold: {counts[10]}"
