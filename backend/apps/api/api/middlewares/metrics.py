"""Prometheus-client HTTP request metrics middleware for Receipt.

Metrics (Wave-17 C1 canonical exposition):
- `receipt_events_ingested_total{kind,flagged}` — business counter, incremented
  by the events router (not here). Kept at module scope so the router can
  `.labels(...).inc()` it.
- `http_requests_total{method,path,status}` — canonical Prom name. Counter
  incremented by this middleware on every HTTP response.
- `http_request_duration_seconds{method,path,status}` — canonical Prom name.
  Histogram with the standard client-default buckets plus 10.0s, observed via
  `time.perf_counter()`. `path` carries the route template (e.g.
  `/sessions/{session_id}`), never the raw path — cardinality-safe.

Exposition:
- `render_prometheus() -> bytes` returns `generate_latest()` output (wire-ready).
  main.py's /metrics handler sets `media_type=CONTENT_TYPE_LATEST` so Starlette
  emits `text/plain; version=0.0.4; charset=utf-8`.

Unmatched routes collapse to a single `path="unmatched"` label to keep
404-scanner traffic visible without cardinality explosion. `/metrics` itself
is excluded (scrape traffic is noise, not signal).
"""
from __future__ import annotations

import os
import secrets
import time
from typing import Iterable

from fastapi import HTTPException, Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "CONTENT_TYPE_LATEST",
    "RequestMetricsMiddleware",
    "http_request_duration_seconds",
    "http_requests_total",
    "receipt_events_ingested_total",
    "render_prometheus",
    "require_metrics_access",
]

receipt_events_ingested_total = Counter(
    "receipt_events_ingested_total",
    "events accepted by /sessions/events",
    ["kind", "flagged"],
)

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

_EXCLUDE_PATHS: frozenset[str] = frozenset({"/metrics"})


def render_prometheus() -> bytes:
    """Return the Prometheus exposition snapshot as bytes (wire-ready)."""
    return generate_latest()


def require_metrics_access(request: Request) -> None:
    """Gate GET /metrics: internal request-rate/latency data, not for public
    exposure. With `METRICS_TOKEN` set, a matching bearer token is required
    (401 otherwise). Without it, production (`ENVIRONMENT=production`) 404s
    the route entirely — indistinguishable from a route that doesn't exist —
    unless the operator opts in with `METRICS_PUBLIC=1`. Dev/local (no
    ENVIRONMENT=production) stays open by default for local scraping."""
    token = os.getenv("METRICS_TOKEN", "").strip()
    if token:
        auth = request.headers.get("authorization", "")
        provided = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not secrets.compare_digest(token, provided):
            raise HTTPException(status_code=401, detail="Invalid or missing metrics token")
        return
    is_production = os.getenv("ENVIRONMENT", "").strip().lower() in ("prod", "production")
    metrics_public = os.getenv("METRICS_PUBLIC", "").strip() == "1"
    if is_production and not metrics_public:
        raise HTTPException(status_code=404, detail="Not Found")


class RequestMetricsMiddleware:
    def __init__(self, app: ASGIApp, exclude_paths: Iterable[str] | None = None) -> None:
        self.app = app
        self.exclude_paths = (
            frozenset(exclude_paths) if exclude_paths is not None else _EXCLUDE_PATHS
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("path", "") or ""
        if raw_path in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        status_box: dict[str, int] = {}

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_box["status"] = int(message["status"])
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, _send)
        finally:
            duration = time.perf_counter() - start
            status = status_box.get("status")
            if status is not None:
                route = scope.get("route")
                template = getattr(route, "path", None) or "unmatched"
                method = scope.get("method", "") or ""
                status_str = str(status)
                http_requests_total.labels(
                    method=method, path=template, status=status_str
                ).inc()
                http_request_duration_seconds.labels(
                    method=method, path=template, status=status_str
                ).observe(duration)
