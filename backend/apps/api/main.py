"""FastAPI entry point — Receipt v0.

Scope: session ingestion + list/detail/summary APIs only. The Yoru
machinery (auth, multi-tenancy, subscriptions, tracing, redis, email) is
disabled — Receipt doesn't need it for the overnight wedge. Original
main.py is preserved at main_saasforge.py.bak for post-v0 reactivation.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from apps.api.api.core.errors import install_error_handlers
from apps.api.api.middleware.security_headers import SecurityHeadersMiddleware
from apps.api.api.middleware.structured_logging import (
    StructuredLoggingMiddleware,
    configure_structured_logger,
)
from apps.api.api.middleware.csrf import CsrfMiddleware
from apps.api.api.middlewares.metrics import (
    CONTENT_TYPE_LATEST,
    RequestMetricsMiddleware,
    render_prometheus,
)
from apps.api.api.middlewares.rate_limit import RateLimitMiddleware
from apps.api.api.routers.admin.groups.router import AdminGroupsRouter
from apps.api.api.routers.admin.notifications_admin_router import NotificationAdminRouter
from apps.api.api.routers.admin.organizations.router import AdminOrganizationsRouter
from apps.api.api.routers.admin.webhooks.router import AdminWebhooksRouter
from apps.api.api.routers.auth.cookie_router import CookieAuthRouter
from apps.api.api.routers.billing.checkout import CheckoutRouter
from apps.api.api.routers.billing.portal import PortalRouter
from apps.api.api.routers.billing.webhook import WebhookRouter
from apps.api.api.routers.features.router import FeaturesRouter
from apps.api.api.routers.grants.router import GrantsRouter
from apps.api.api.routers.heartbeat.router import HeartbeatRouter
from apps.api.api.routers.invitations.router import InvitationsRouter
from apps.api.api.routers.me.router import MeRouter
from apps.api.api.routers.notifications.router import NotificationRouter
from apps.api.api.routers.ops_router import OpsRouter
from apps.api.api.routers.organizations.router import OrganizationsRouter
from apps.api.api.routers.ping_router import PingRouter
from apps.api.api.routers.plans.router import PlansRouter
from apps.api.api.routers.promo.router import PromoRouter
from apps.api.api.routers.sales.contact import SalesContactRouter
from apps.api.api.routers.subscriptions.router import SubscriptionsRouter
from apps.api.api.routers.receipt.auth_router import AuthRouter
from apps.api.api.routers.receipt.dashboard_router import DashboardRouter
from apps.api.api.routers.receipt.db import init_db
from apps.api.api.routers.receipt.events_router import EventsRouter
from apps.api.api.routers.receipt.export_router import ExportRouter
from apps.api.api.routers.receipt.health_deep_router import HealthDeepRouter
from apps.api.api.routers.receipt.sessions_router import SessionsRouter
from apps.api.api.routers.receipt.pricing_router import PricingRouter
from apps.api.api.routers.receipt.summary_router import SummaryRouter
from apps.api.api.routers.webhooks import WebhooksRouter
from apps.api.core.logging import configure_logging, get_logger
from apps.api.core.max_body_size import MaxBodySizeMiddleware
from apps.api.core.sentry import init_sentry

configure_logging()

_logger = get_logger(__name__)
_logger.info("sentry_initialized", extra={"initialized": init_sentry()})


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_structured_logger()
    init_db()
    # Warm the pricing table (disk cache if fresh, else LiteLLM fetch). Failing
    # is non-fatal — lookup_rates() will use the static fallback.
    try:
        from apps.api.api.routers.receipt.pricing import refresh as _refresh_pricing
        _refresh_pricing(force=False)
    except Exception as exc:  # pragma: no cover — never block boot on pricing
        _logger.warning("pricing_warmup_failed", extra={"error": str(exc)})
    yield


# Single source of truth for the running version: the RECEIPT_VERSION build arg
# (set at docker-compose / release build time), defaulting to the current release.
# Exposed on GET /api/v1/config so the CLI's `yoru update --server` can compare it
# against the latest TsukumoHQ/yoru release tag.
_API_VERSION = os.getenv("RECEIPT_VERSION", "0.2.1")

app = FastAPI(
    lifespan=lifespan,
    title="Receipt API",
    version=_API_VERSION,
    description="Audit-grade session receipts for autonomous AI coding agents.",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Security headers — innermost middleware (first add_middleware = last on
# request, first on response). Stamps the 6-header bundle onto every response
# before CORS layers its headers on top. wave-50 C3.
app.add_middleware(SecurityHeadersMiddleware)

_cors_origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        # 5173 = dashboard dev, 5174 = marketing dev (public /s/:id fetches
        # the unauth endpoint from the marketing origin), 3000 = legacy.
        "http://localhost:5173,http://localhost:5174,http://localhost:3000",
    ).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# CSRF double-submit cookie. Only enforces on state-changing requests that
# carry a session cookie — CLI bearer auth bypasses (immune by construction).
# Signup/signin/refresh are exempt because they SET the cookies.
app.add_middleware(
    CsrfMiddleware,
    exempt_prefixes=[
        "/api/v1/sessions/events",      # CLI hook-token ingest, bearer auth
        "/api/v1/billing/webhook",      # Polar webhook, signature-authenticated
        "/api/v1/sales/contact",        # public form from marketing/, honeypot + rate-limit
    ],
)

# Rate-limit ONLY the ingest endpoint (CLI hooks can batch up to 1000 events
# per request — runaway hooks must not hammer us). SPA reads stay unthrottled.
app.add_middleware(RateLimitMiddleware)

# Fail fast on oversized POST bodies (256 KiB default) on ingest paths BEFORE
# CORS/RateLimit/app so a multi-MB attacker payload never reaches Pydantic's
# `max_length=1000` array guard. Scoped to POST + the three ingest paths only.
app.add_middleware(MaxBodySizeMiddleware)

# Mint/propagate X-Request-ID and emit one structured JSON log per request.
# wave-40 C1: schema is {timestamp, level, request_id, method, path, status,
# latency_ms, user_id}. C2 will front this middleware with a dedicated
# correlation-ID middleware that reads X-Request-ID from the inbound request.
app.add_middleware(StructuredLoggingMiddleware)

# Outermost: in-process request counter (sees status from every layer below).
app.add_middleware(RequestMetricsMiddleware)

# Global JSON error envelope + X-Request-ID propagation on error responses.
# Exception goes to ServerErrorMiddleware (outermost); others to ExceptionMiddleware.
install_error_handlers(app)

ping_router = PingRouter()
ping_router.initialize_services()
app.include_router(ping_router.get_router())

events_router = EventsRouter()
events_router.initialize_services()
app.include_router(events_router.get_router(), prefix="/api/v1")

# ExportRouter MUST mount before SessionsRouter: `/sessions/export` would
# otherwise be swallowed by `/sessions/{session_id}`.
export_router = ExportRouter()
app.include_router(export_router.get_router(), prefix="/api/v1")

sessions_router = SessionsRouter()
app.include_router(sessions_router.get_router(), prefix="/api/v1")

# Issue #79 — public (unauth) reader for sessions flipped public via
# /sessions/{id}/share. Mounted separately so the /public/ prefix lives
# next to /sessions/ without the authed router knowing about it.
from apps.api.api.routers.receipt.public_sessions_router import PublicSessionsRouter  # noqa: E402
public_sessions_router = PublicSessionsRouter()
app.include_router(public_sessions_router.get_router(), prefix="/api/v1")

# Issue #79 — per-user share-disclosure consent (GET + POST).
# Persists the one-time "I understand what public means" ack on the
# users row so it travels with the account instead of per-device state.
from apps.api.api.routers.receipt.consent_router import ShareConsentRouter  # noqa: E402
share_consent_router = ShareConsentRouter()
app.include_router(share_consent_router.get_router(), prefix="/api/v1")

summary_router = SummaryRouter()
summary_router.initialize_services()
app.include_router(summary_router.get_router(), prefix="/api/v1")

pricing_router = PricingRouter()
app.include_router(pricing_router.get_router(), prefix="/api/v1")

auth_router = AuthRouter()
app.include_router(auth_router.get_router(), prefix="/api/v1")

# First-run onboarding (/api/v1/setup/*). Unauthenticated by necessity; locks
# itself once an admin exists. Shares its core with the `make setup` CLI.
from apps.api.api.routers.setup.router import SetupRouter  # noqa: E402

setup_router = SetupRouter()
app.include_router(setup_router.get_router(), prefix="/api/v1")

# Dashboard cookie-based auth (Supabase-backed signup/signin/me/refresh/signout).
# Sits alongside the Receipt CLI hook-token router above — both mounted under
# /api/v1/auth, but routes don't collide (CLI uses /hook-token*, dashboard uses
# /signup, /signin, /session/*).
cookie_auth_router = CookieAuthRouter()
# AuthService is lazy-initialized in handlers — defer so the import doesn't
# hard-fail when SUPABASE_URL is unset in a boot path that never touches auth.
app.include_router(cookie_auth_router.get_router(), prefix="/api/v1")

# Yoru SaaS surface — canonical routers from the forge template. All
# mounted now that the underlying schemas (profiles / plans / orgs / groups /
# subscriptions / invitations / notifications / webhooks) live on Supabase.
# Auth deps (`get_current_user_id`, `get_current_user_token`) are poly —
# cookie (dashboard) OR bearer (CLI) both reach here.
app.include_router(MeRouter().get_router(), prefix="/api/v1")
app.include_router(OrganizationsRouter().get_router(), prefix="/api/v1")
app.include_router(InvitationsRouter().get_router(), prefix="/api/v1")
app.include_router(SubscriptionsRouter().get_router(), prefix="/api/v1")
app.include_router(PlansRouter().get_router(), prefix="/api/v1")
app.include_router(FeaturesRouter().get_router(), prefix="/api/v1")
app.include_router(PromoRouter().get_router(), prefix="/api/v1")
app.include_router(GrantsRouter().get_router(), prefix="/api/v1")
app.include_router(NotificationRouter().get_router(), prefix="/api/v1")
app.include_router(HeartbeatRouter().get_router(), prefix="/api/v1")

# Admin surface — require role=admin in profiles via require_admin dep.
app.include_router(AdminOrganizationsRouter().get_router(), prefix="/api/v1")
app.include_router(AdminGroupsRouter().get_router(), prefix="/api/v1")
app.include_router(AdminWebhooksRouter().get_router(), prefix="/api/v1")
app.include_router(NotificationAdminRouter().get_router(), prefix="/api/v1")

# Self-host control panel: admin manages users + SMTP without a cloud console.
from apps.api.api.routers.admin.instance.router import InstanceAdminRouter  # noqa: E402
app.include_router(InstanceAdminRouter().get_router(), prefix="/api/v1")

dashboard_router = DashboardRouter()
app.include_router(dashboard_router.get_router(), prefix="/api/v1")

# Billing + sales are hosted-only. A self-hosted instance (BILLING_ENABLED unset)
# never mounts the Stripe/Polar checkout/portal/webhook or the contact-sales form,
# so there is no dead/dangerous payment surface and no broken "Upgrade" CTA.
_BILLING_ENABLED = os.getenv("BILLING_ENABLED", "false").strip().lower() in ("1", "true", "yes")
if _BILLING_ENABLED:
    checkout_router = CheckoutRouter()
    app.include_router(checkout_router.get_router(), prefix="/api/v1/billing")

    portal_router = PortalRouter()
    app.include_router(portal_router.get_router(), prefix="/api/v1/billing")

    billing_webhook_router = WebhookRouter()
    app.include_router(billing_webhook_router.get_router(), prefix="/api/v1/billing")

    # Public Contact-Sales form — unauth, rate-limited, honeypot-protected.
    sales_contact_router = SalesContactRouter()
    app.include_router(sales_contact_router.get_router(), prefix="/api/v1")

webhooks_router = WebhooksRouter()
app.include_router(webhooks_router.get_router(), prefix="/api/v1")


@app.get("/api/v1/config", tags=["config"], summary="Public instance configuration")
def instance_config() -> dict:
    """Runtime flags the SPA needs to shape itself for this deployment.

    Lets the dashboard hide billing/upgrade/multi-tenant UI on a self-hosted
    instance instead of shipping dead CTAs."""
    return {
        "version": _API_VERSION,
        "billing_enabled": _BILLING_ENABLED,
        "auth_provider": os.getenv("AUTH_PROVIDER", "local").strip().lower(),
        "instance_name": os.getenv("INSTANCE_NAME", "Yoru"),
        "single_org": not _BILLING_ENABLED,
    }

health_deep_router = HealthDeepRouter()
app.include_router(health_deep_router.get_router(), prefix="/api/v1")

ops_router = OpsRouter()
app.include_router(ops_router.get_router())


@app.get("/", tags=["root"])
async def root():
    return {
        "message": "Receipt API",
        "docs": "/api/docs",
        "health": "/health",
    }


@app.get("/metrics", tags=["ops"])
async def metrics() -> Response:
    return Response(
        content=render_prometheus(),
        media_type=CONTENT_TYPE_LATEST,
    )
