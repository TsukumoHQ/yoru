"""Prometheus-client metrics exposition tests (Wave-13 C2).

Mounts a minimal FastAPI app with RequestMetricsMiddleware + EventsRouter +
a /metrics endpoint that mirrors main.py's handler, so these tests are
self-contained and don't depend on the full main app's middleware stack.
"""
from __future__ import annotations

import os

# Point receipt DB at a fresh in-memory sqlite BEFORE importing the package.
os.environ["RECEIPT_DB_URL"] = "sqlite:///:memory:"

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from apps.api.api.middlewares.metrics import (  # noqa: E402
    CONTENT_TYPE_LATEST,
    RequestMetricsMiddleware,
    http_request_duration_seconds,
    http_requests_total,
    receipt_events_ingested_total,
    render_prometheus,
)
from apps.api.api.routers.receipt import db as receipt_db  # noqa: E402
from apps.api.api.routers.receipt import models  # noqa: F401,E402
from apps.api.api.routers.receipt.events_router import EventsRouter  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_metrics() -> Iterator[None]:
    """Prometheus registries are process-global; clear label children per test."""
    receipt_events_ingested_total._metrics.clear()
    http_requests_total._metrics.clear()
    http_request_duration_seconds._metrics.clear()
    yield


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    old = receipt_db.engine
    receipt_db.engine = eng
    try:
        yield eng
    finally:
        receipt_db.engine = old


@pytest.fixture()
def client(engine) -> TestClient:
    app = FastAPI()
    app.include_router(EventsRouter().get_router(), prefix="/api/v1")

    @app.get("/metrics")
    async def _metrics() -> Response:
        return Response(content=render_prometheus(), media_type=CONTENT_TYPE_LATEST)

    # Outermost middleware so it wraps every request (including /metrics,
    # which self-excludes inside the middleware).
    app.add_middleware(RequestMetricsMiddleware)

    def _override():
        with Session(engine) as s:
            yield s

    from apps.api.api.routers.receipt.db import get_session

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def test_metrics_endpoint_content_type_and_200(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # Starlette appends charset=utf-8 to CONTENT_TYPE_LATEST ('text/plain;
    # version=0.0.4; charset=utf-8'). We only assert the canonical prefix to
    # stay resilient to the prometheus-client version bump in pyproject.
    ctype = resp.headers["content-type"]
    assert ctype.startswith("text/plain"), ctype
    assert "version=0.0.4" in ctype
    assert "charset=utf-8" in ctype


def test_events_ingested_counter_increments_on_flagged_write(
    client: TestClient, engine
) -> None:
    # `.env` file Write trips the `env_mutation` red flag → per-event flagged=true.
    # EventKind is inferred from tool="Write" → "file_change".
    # M2b: ingest requires a credential — mint a bearer token; attribution then
    # comes from it (no body `user`).
    import hashlib
    import secrets
    import uuid

    raw = f"rcpt_{secrets.token_urlsafe(24)}"
    with Session(engine) as s:
        s.add(models.HookToken(
            id=uuid.uuid4().hex, user="met-smoke@test.local",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        ))
        s.commit()
    headers = {"Authorization": f"Bearer {raw}"}
    payload = {
        "events": [
            {
                "session_id": "met-smoke-t",
                "tool": "Write",
                "path": ".env",
                "content": "K=V",
            }
        ]
    }
    ingest = client.post("/api/v1/sessions/events", json=payload, headers=headers)
    assert ingest.status_code == 202, ingest.text

    body = client.get("/metrics").text

    # Prometheus sorts labels alphabetically: {flagged="...",kind="..."}.
    expected = (
        'receipt_events_ingested_total{flagged="true",kind="file_change"}'
    )
    matching = [line for line in body.splitlines() if line.startswith(expected)]
    assert matching, f"no flagged file_change counter line in body:\n{body}"
    count = float(matching[0].rsplit(" ", 1)[-1])
    assert count >= 1.0, f"expected counter >= 1, got {count}"

    # HTTP counters + histogram are also emitted by the middleware.
    assert "http_requests_total{" in body
    assert "http_request_duration_seconds_bucket{" in body


def test_metrics_prom_exposition_format_valid(client: TestClient) -> None:
    """Wave-17 C1 acceptance: canonical HELP/TYPE + data lines for both series."""
    # Drive one request so at least one label set exists for each metric.
    driver = client.get("/api/v1/sessions?limit=1")
    # EventsRouter doesn't mount /sessions GET; we just need SOMETHING that
    # returns via the middleware to populate counters.
    assert driver.status_code in (200, 404, 405), driver.text

    resp = client.get("/metrics")
    assert resp.status_code == 200
    ctype = resp.headers["content-type"]
    assert ctype.startswith("text/plain"), ctype

    body = resp.text
    assert "# HELP http_requests_total " in body
    assert "# TYPE http_requests_total counter" in body
    assert "# HELP http_request_duration_seconds " in body
    assert "# TYPE http_request_duration_seconds histogram" in body

    # At least one counter data line (non-# prefixed) for http_requests_total.
    counter_lines = [
        ln for ln in body.splitlines()
        if ln.startswith("http_requests_total{") and "}" in ln and " " in ln.rsplit("}", 1)[-1]
    ]
    assert counter_lines, f"no http_requests_total data line:\n{body}"

    # At least one histogram bucket series for http_request_duration_seconds.
    bucket_lines = [
        ln for ln in body.splitlines()
        if ln.startswith("http_request_duration_seconds_bucket{") and 'le="' in ln
    ]
    assert bucket_lines, f"no http_request_duration_seconds_bucket data line:\n{body}"

    # Cardinality-safety: buckets include canonical 10.0 boundary + +Inf.
    assert any('le="10.0"' in ln for ln in bucket_lines), "missing le=10.0 bucket"
    assert any('le="+Inf"' in ln for ln in bucket_lines), "missing le=+Inf bucket"


def test_metrics_labels_use_route_template_not_raw_path() -> None:
    """Three requests to a parametric route must collapse to ONE path label line."""
    # Fresh mini-app with a parametric route — self-contained, no DB.
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def _get_item(item_id: str) -> dict:
        return {"id": item_id}

    @app.get("/metrics")
    async def _metrics() -> Response:
        return Response(content=render_prometheus(), media_type=CONTENT_TYPE_LATEST)

    app.add_middleware(RequestMetricsMiddleware)
    c = TestClient(app)

    # Reset label children so this test sees a clean slate.
    http_requests_total._metrics.clear()
    http_request_duration_seconds._metrics.clear()

    for item_id in ("abc", "def", "xyz"):
        r = c.get(f"/items/{item_id}")
        assert r.status_code == 200

    body = c.get("/metrics").text

    # Assert exactly ONE http_requests_total line carries the route template,
    # and NO line carries a raw id.
    template_lines = [
        ln for ln in body.splitlines()
        if ln.startswith("http_requests_total{") and 'path="/items/{item_id}"' in ln
    ]
    assert len(template_lines) == 1, (
        f"expected 1 template-path counter line, got {len(template_lines)}:\n"
        + "\n".join(template_lines)
    )

    raw_lines = [
        ln for ln in body.splitlines()
        if ln.startswith("http_requests_total{")
        and any(f'path="/items/{rid}"' in ln for rid in ("abc", "def", "xyz"))
    ]
    assert not raw_lines, "raw path labels leaked into metrics:\n" + "\n".join(raw_lines)

    # The template line must report count >= 3.
    count = float(template_lines[0].rsplit(" ", 1)[-1])
    assert count >= 3.0, f"expected >= 3 requests counted, got {count}"
