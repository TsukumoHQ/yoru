# Receipt API — multi-stage production image (vault/DEPLOY-V0.md §1(a))
#
# Build from the REPO ROOT context (the backend now has a real runtime dep
# on the sibling `packages/yoru-contract/`, DEC-yoru-design-ruling-1 §C —
# same reason frontend/Dockerfile already builds from root for `packages/
# receipt-ui/`), e.g.:
#   docker build -f backend/Dockerfile -t receipt-api:dev .
# Run:    docker run --rm -p 8002:8002 receipt-api:dev

# ============================================================================
# Stage 1 — builder: resolve deps with uv into /opt/venv
# ============================================================================
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/usr/local/bin/python3.11

# uv is the only build-time dep we need. No build-essential — wheels are
# available for every prod dependency on linux/amd64 + linux/arm64.
RUN pip install --no-cache-dir "uv==0.4.30"

WORKDIR /build

# Copy lockfile + manifest first so a deps-only change rebuilds this layer
# but a source-only change does not. yoru-contract's manifest too — it's a
# real runtime dep (`red_flags.py` imports it), resolved via the path-dep
# in [tool.uv.sources]; its `pyproject.toml` alone is enough for `uv sync`
# to resolve versions, the actual source is copied in below.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
COPY packages/yoru-contract/pyproject.toml packages/yoru-contract/README.md ../packages/yoru-contract/

# --no-dev drops pytest, pytest-asyncio, ruff. --frozen fails fast if uv.lock
# is stale (better than silent drift). [tool.uv] package=false means uv only
# installs deps; source is mounted at runtime via PYTHONPATH=/app. The
# path-dep needs yoru-contract's actual source present too (a build backend
# reads it even to resolve, not just to install) — copy it in before sync.
COPY packages/yoru-contract/src ../packages/yoru-contract/src
RUN uv sync --no-dev --frozen --no-install-project

# ============================================================================
# Stage 2 — runtime: slim image with venv + source + non-root user
# ============================================================================
FROM python:3.11-slim AS runtime

# Build-time metadata baked into the image. Read by GET /api/version (see §1(d)).
# Override at build with: --build-arg RECEIPT_VERSION=0.1.0 --build-arg RECEIPT_GIT_SHA=$(git rev-parse --short HEAD)
ARG RECEIPT_VERSION="0.1.0-dev"
ARG RECEIPT_GIT_SHA="unknown"
ARG RECEIPT_BUILT_AT="unknown"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH" \
    RECEIPT_VERSION=${RECEIPT_VERSION} \
    RECEIPT_GIT_SHA=${RECEIPT_GIT_SHA} \
    RECEIPT_BUILT_AT=${RECEIPT_BUILT_AT}

# Non-root user. UID 1000 matches the host dev user in most cases — keeps
# bind-mounted volumes (./backend/data) writable without chown.
RUN groupadd --system --gid 1000 appuser \
    && useradd  --system --uid 1000 --gid appuser --home-dir /app --no-create-home appuser

WORKDIR /app

# Pre-create writable dirs as appuser so SQLite init at lifespan startup works
# even when no host volume is mounted.
RUN mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app

# Pull the resolved venv from the builder. Single COPY = single layer.
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv

# Source. apps/ holds the FastAPI app; libs/ holds log_manager (the only
# libs.* module main.py and its router tree actually import). Other libs.*
# subpackages travel along but are dormant — safe per ARCHITECTURE.md §2.
COPY --chown=appuser:appuser backend/apps /app/apps
COPY --chown=appuser:appuser backend/libs /app/libs

USER appuser

EXPOSE 8002

# Healthcheck uses stdlib (no curl install needed). 3s timeout matches /health
# being a synchronous in-process probe.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request,sys; \
sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8002/health', timeout=3).status == 200 else sys.exit(1)"

CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8002"]
