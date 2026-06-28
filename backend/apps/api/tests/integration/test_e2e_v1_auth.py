"""E2E auth-hardening guards against live :8002 — wave-39-E1, updated post-CVE.

ORIGINAL premise (wave-39-E1): `POST /api/v1/auth/hook-token` was an *unauth*
proxy that bootstrapped identity from `body.user` and minted a bearer in one
round-trip — used to emulate signup→login. That endpoint was later HARDENED:
`AuthRouter.mint_token` now requires an authenticated caller
(`Depends(require_current_user)`) and IGNORES `body.user` (see the CVE note in
the handler docstring — trusting `body.user` let any caller mint a token for
any identity). So the old "unauth mint = signup proxy" flow no longer exists.

These tests now guard the HARDENED contract instead: the Receipt CLI auth
surface must reject unauthenticated / invalid callers.

    POST /auth/hook-token        with no auth      → 401  (the CVE regression guard)
    GET  /auth/hook-tokens       with bad bearer   → 401
    GET  /auth/hook-tokens       with no auth      → 401
    POST /auth/logout            with no auth      → 401

They run only when a backend answers on E2E_BASE_URL (skip otherwise, e.g. CI).

DB safety (NON-NEGOTIABLE, per self-learning §DB-WIPE-INCIDENT): the existing
conftest.clean_db autouse fixture truncates events / sessions / hook_tokens
against the live engine — running it against :8002's pid would wipe
`backend/data/receipt.db`. We OVERRIDE it locally with a no-op. These guards
are read-only / rejected writes, so they never mutate the live process's rows.
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest


@pytest.fixture(autouse=True)
def clean_db():
    """Override conftest.clean_db — DB-WIPE-INCIDENT (NON-NEGOTIABLE).

    Receipt's DB lives at backend/data/receipt.db; the live :8002 process
    serves real sessions out of it. These E2E tests MUST NOT delete rows.
    Per-test isolation is via unique email suffix, not table truncation.
    """
    yield


@pytest.fixture(scope="module")
def backend_base_url() -> str:
    return os.environ.get("E2E_BASE_URL", "http://localhost:8002")


@pytest.fixture(scope="module")
def backend_up(backend_base_url: str) -> bool:
    try:
        r = httpx.get(f"{backend_base_url}/health", timeout=2.0)
    except httpx.HTTPError as exc:
        pytest.skip(f"backend not up on {backend_base_url}: {exc}")
    if r.status_code != 200:
        pytest.skip(
            f"backend not up on {backend_base_url}: /health -> {r.status_code}"
        )
    return True


@pytest.fixture
def http(backend_base_url: str, backend_up: bool):
    with httpx.Client(base_url=backend_base_url, timeout=10.0) as c:
        yield c


def _unique_email(prefix: str = "e2e-auth") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.local"


def test_unauth_mint_rejected(http: httpx.Client) -> None:
    """CVE regression guard: minting a hook-token with NO authentication must
    be rejected. The pre-hardening endpoint trusted `body.user` and minted a
    bearer for arbitrary identities; the hardened handler requires an
    authenticated caller (`require_current_user`) and ignores `body.user`.
    """
    user = _unique_email()
    r = http.post(
        "/api/v1/auth/hook-token",
        json={"user": user, "label": "e2e-auth-flow"},
    )
    assert r.status_code == 401, (
        f"unauth mint must be rejected (CVE: trusting body.user); got "
        f"{r.status_code}: {r.text}"
    )


def test_unknown_bearer_rejected(http: httpx.Client) -> None:
    """A syntactically valid `rcpt_*` bearer that isn't in the DB must be
    rejected by the caller-scoped list endpoint.
    """
    fake_bearer = "rcpt_" + uuid.uuid4().hex + uuid.uuid4().hex
    me = http.get(
        "/api/v1/auth/hook-tokens",
        headers={"Authorization": f"Bearer {fake_bearer}"},
    )
    assert me.status_code == 401, me.text


def test_unauth_list_and_logout_rejected(http: httpx.Client) -> None:
    """The caller-scoped list and the logout endpoint both require auth — an
    unauthenticated request to either is rejected.
    """
    me = http.get("/api/v1/auth/hook-tokens")
    assert me.status_code == 401, me.text

    out = http.post("/api/v1/auth/logout")
    assert out.status_code == 401, out.text
