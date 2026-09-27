"""GET /metrics access gating (task 78077378, AC3).

Before: /metrics was wide open — no token, no environment check — so
request-rate/latency data (including path templates) leaked to anyone who
could reach the instance.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from apps.api.api.middlewares.metrics import (
    CONTENT_TYPE_LATEST,
    render_prometheus,
    require_metrics_access,
)


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()

    @app.get("/metrics", dependencies=[Depends(require_metrics_access)])
    async def _metrics() -> Response:
        return Response(content=render_prometheus(), media_type=CONTENT_TYPE_LATEST)

    return TestClient(app)


def test_open_by_default_in_dev(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("METRICS_PUBLIC", raising=False)
    assert client.get("/metrics").status_code == 200


def test_production_without_token_is_404(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("METRICS_PUBLIC", raising=False)
    r = client.get("/metrics")
    assert r.status_code == 404


def test_production_with_metrics_public_stays_open(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("METRICS_TOKEN", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("METRICS_PUBLIC", "1")
    assert client.get("/metrics").status_code == 200


def test_token_set_requires_matching_bearer(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("METRICS_TOKEN", "s3cr3t")
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    assert client.get("/metrics").status_code == 401
    assert client.get(
        "/metrics", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    assert client.get(
        "/metrics", headers={"Authorization": "Bearer s3cr3t"}
    ).status_code == 200


def test_token_set_wins_over_production_gate(client: TestClient, monkeypatch) -> None:
    """A configured token is sufficient even in production — the operator
    opted into scraping via a credential, not the public-exposure escape
    hatch."""
    monkeypatch.setenv("METRICS_TOKEN", "s3cr3t")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("METRICS_PUBLIC", raising=False)
    r = client.get("/metrics", headers={"Authorization": "Bearer s3cr3t"})
    assert r.status_code == 200
