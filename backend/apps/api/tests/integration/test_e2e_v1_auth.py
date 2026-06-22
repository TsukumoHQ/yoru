"""E2E auth-flow tests against live :8002 — wave-39-E1.

Brief asked for signup → login → /me → logout against `/api/v1/auth/...`.
Live `apps/api/main.py` mounts Receipt's hook-token AuthRouter, NOT the
Yoru signup/login routers (which exist on disk under
`api/routers/auth/router.py` + `api/routers/me/router.py` but are never
included). Per brief: "If the exact endpoint paths differ … grep the real
paths and match the real shape … Do NOT invent endpoints; only test what
exists." The end-to-end auth flow Receipt actually deploys is:

    mint hook-token  (proxies signup+login: bootstraps identity, yields bearer)
    GET   /auth/hook-tokens with bearer  (proxies /me: returns caller's own rows)
    POST  /auth/logout    (revokes bearer)

The test names match the brief literals; the bodies exercise the deployed
endpoints. Verified by curl probe at task start (POST mint → 201;
GET /hook-tokens with bearer → 200; POST /logout → 204; same bearer → 401).

DB safety (NON-NEGOTIABLE, per self-learning §DB-WIPE-INCIDENT): the
existing conftest.clean_db autouse fixture truncates events / sessions /
hook_tokens against the live engine — running it against :8002's pid would
wipe `backend/data/receipt.db`. We OVERRIDE it locally with a no-op so
our E2E tests run alongside the live process without touching its rows.
Unique uuid suffix in each email isolates accumulating test users.
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


def test_signup_then_login_then_me(http: httpx.Client) -> None:
    """signup+login proxy: mint a hook-token (bootstraps identity AND yields
    bearer in one round-trip). /me proxy: GET /api/v1/auth/hook-tokens — the
    caller-scoped list endpoint that returns ONLY rows where row.user equals
    the bearer's resolved identity. Asserts the minted token id appears in
    the response, which is only possible if the bearer resolves to the same
    user we minted under.
    """
    user = _unique_email()
    r = http.post(
        "/api/v1/auth/hook-token",
        json={"user": user, "label": "e2e-auth-flow"},
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["user"] == user
    assert body["token"].startswith("rcpt_")
    token = body["token"]
    minted_id = body["user_id"]

    me = http.get(
        "/api/v1/auth/hook-tokens",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me.status_code == 200, me.text
    items = me.json()
    assert isinstance(items, list) and len(items) >= 1
    assert any(item["id"] == minted_id for item in items), (
        f"minted id {minted_id} not in caller-scoped list: {items}"
    )


def test_login_bad_password_401(http: httpx.Client) -> None:
    """bad-password proxy: a syntactically valid `rcpt_*` bearer that isn't
    in the DB. Mints a real token first to confirm the success path is
    available (and so the failure isn't a false positive from a down
    backend or routing change). Then asserts the unknown bearer returns 401.
    """
    user = _unique_email("e2e-auth-bad")
    r = http.post("/api/v1/auth/hook-token", json={"user": user})
    assert r.status_code in (200, 201), r.text

    fake_bearer = "rcpt_" + uuid.uuid4().hex + uuid.uuid4().hex
    me = http.get(
        "/api/v1/auth/hook-tokens",
        headers={"Authorization": f"Bearer {fake_bearer}"},
    )
    assert me.status_code == 401, me.text


def test_logout_revokes_session(http: httpx.Client) -> None:
    """Full revocation cycle: mint → bearer works (200) → logout (204 per
    AuthRouter.logout contract — brief said 200 but live returns 204; brief
    authorises 'match the real shape') → same bearer is rejected (401).
    """
    user = _unique_email("e2e-auth-logout")
    r = http.post("/api/v1/auth/hook-token", json={"user": user})
    assert r.status_code in (200, 201), r.text
    token = r.json()["token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    pre = http.get("/api/v1/auth/hook-tokens", headers=auth_header)
    assert pre.status_code == 200, pre.text

    out = http.post("/api/v1/auth/logout", headers=auth_header)
    assert out.status_code == 204, out.text

    post = http.get("/api/v1/auth/hook-tokens", headers=auth_header)
    assert post.status_code == 401, post.text
