"""Tests for batch sessions export — §4.6 JSONL + CSV shapes.

Covers the task-brief acceptance criteria:
  - JSONL: one session per line, events inline, caller-scoped
  - CSV: exact column order per task spec
  - `flagged_only=true` filter
  - 10k cap + `X-Truncated` header
"""
from __future__ import annotations

import base64
import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from sqlmodel import Session as SQLSession

from apps.api.api.routers.receipt.models import Event
from apps.api.api.routers.receipt.models import Session as SessionRow
from apps.api.api.services.signing import dsse

# Fixed KAT seed (see test_signing_dsse.YORU_DOKAN_KAT) — used to sign bundles
# deterministically in tests without minting a real per-install key.
_TEST_SEED_B64 = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="

BASE_TS = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)

_CSV_COLUMNS = [
    "user_email", "started_at", "ended_at", "duration_sec",
    "tools_count", "files_count", "cost_usd",
    "flagged", "flags_csv", "summary",
]


@pytest.fixture()
def app(engine) -> FastAPI:
    """Mount ONLY the ExportRouter so this module runs independently."""
    from apps.api.api.routers.receipt.db import get_session
    from apps.api.api.routers.receipt.export_router import ExportRouter

    _app = FastAPI()
    _app.include_router(ExportRouter().get_router(), prefix="/api/v1")

    def _override():
        with SQLSession(engine) as s:
            yield s

    _app.dependency_overrides[get_session] = _override
    return _app


@pytest.fixture()
def alice_headers(mint_token):
    _, h = mint_token("alice")
    return h


def _seed(db_session, sid: str, user: str, *, flagged: bool = False,
          flags: list[str] | None = None, offset_sec: int = 0,
          duration_sec: int = 60) -> None:
    started = BASE_TS + timedelta(seconds=offset_sec)
    db_session.add(SessionRow(
        id=sid, user=user,
        started_at=started,
        ended_at=started + timedelta(seconds=duration_sec),
        tools_count=3, files_count=2,
        cost_usd=0.25,
        flagged=flagged, flags=flags or [],
        files_changed=["a.py"], tools_called=["Bash"],
        summary="test summary",
    ))


def test_export_jsonl_shape_and_user_scope(client, db_session, alice_headers, mint_token):
    """JSONL: one session per line, events inline, caller-only sessions."""
    _seed(db_session, "s1", "alice", offset_sec=0)
    _seed(db_session, "s2", "alice", offset_sec=10, flagged=True, flags=["shell_rm"])
    _seed(db_session, "s3", "bob", offset_sec=5)  # must be excluded
    db_session.add(Event(
        session_id="s2", ts=BASE_TS + timedelta(seconds=11),
        kind="tool_use", tool="Bash", content="rm -rf /", flags=["shell_rm"],
    ))
    db_session.commit()

    resp = client.get(
        "/api/v1/sessions/export?format=json", headers=alice_headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    assert resp.headers["x-truncated"] == "false"
    assert 'filename="receipt-export-' in resp.headers["content-disposition"]

    lines = [ln for ln in resp.text.split("\n") if ln]
    assert len(lines) == 2  # alice only, bob excluded
    parsed = [json.loads(ln) for ln in lines]
    ids = {p["session_id"] for p in parsed}
    assert ids == {"s1", "s2"}
    for p in parsed:
        assert p["user_email"] == "alice"
        assert p["duration_sec"] == 60.0
        assert isinstance(p["events"], list)
    s2 = next(p for p in parsed if p["session_id"] == "s2")
    assert s2["flagged"] is True
    assert len(s2["events"]) == 1
    assert s2["events"][0]["content"] == "rm -rf /"


def test_export_csv_shape_and_flagged_only(client, db_session, alice_headers):
    """CSV: exact columns in order; flagged_only filter excludes non-flagged rows."""
    _seed(db_session, "c1", "alice", offset_sec=0)
    _seed(
        db_session, "c2", "alice", offset_sec=10,
        flagged=True, flags=["secret_aws", "shell_rm"],
    )
    db_session.commit()

    resp = client.get(
        "/api/v1/sessions/export?format=csv&flagged_only=true",
        headers=alice_headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["x-truncated"] == "false"
    assert resp.headers["content-disposition"].endswith('.csv"')

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    assert rows[0] == _CSV_COLUMNS
    assert len(rows) == 2  # header + 1 flagged row
    row = rows[1]
    assert row[0] == "alice"
    assert row[3] == "60"  # duration_sec
    assert row[4] == "3"   # tools_count
    assert row[7] == "true"  # flagged
    assert row[8] == "secret_aws,shell_rm"  # flags_csv
    assert row[9] == "test summary"


def test_export_requires_auth(client):
    assert client.get("/api/v1/sessions/export").status_code == 401


def test_export_walls_cross_org(client, db_session, mint_token):
    """M5a (design 44a3774a §3/§6): export is org-walled like the sessions list.

    A CLI-token identity (no profile) is scoped to the default org. Seed a
    default-org session (NULL org_id counts as default) plus a same-user
    other-org session; only the default-org one may be exported — the
    other-org row is walled out even though the caller owns it.
    """
    _raw, headers = mint_token("u-1")
    _seed(db_session, "e-mine", "u-1", offset_sec=0)          # org_id NULL → default
    _seed(db_session, "e-other", "u-1", offset_sec=10)
    db_session.get(SessionRow, "e-other").org_id = "org-acme"  # other tenant
    db_session.commit()

    resp = client.get("/api/v1/sessions/export?format=json", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {json.loads(ln)["session_id"] for ln in resp.text.split("\n") if ln}
    assert ids == {"e-mine"}  # org-acme session walled out of the export


def test_export_eu_ai_act_requires_signing_key(
    client, db_session, alice_headers, monkeypatch
):
    """The signed compliance bundle refuses (409) without a configured key."""
    monkeypatch.delenv("YORU_SIGNING_KEY", raising=False)
    _seed(db_session, "k1", "alice")
    db_session.commit()
    resp = client.get(
        "/api/v1/sessions/export?format=eu-ai-act", headers=alice_headers
    )
    assert resp.status_code == 409


def test_export_eu_ai_act_signed_bundle(
    client, db_session, alice_headers, monkeypatch
):
    """M5b: signed, offline-verifiable, org-walled bundle with chain fields."""
    monkeypatch.setenv("YORU_SIGNING_KEY", _TEST_SEED_B64)
    _seed(db_session, "a1", "alice", flagged=True, flags=["shell_rm"])
    db_session.add(Event(
        session_id="a1", ts=BASE_TS, kind="tool_use", tool="Bash",
        entry_hash="h1", prev_hash=None, chain_version=2,
        content_digest="d1", flags=["shell_rm"],
    ))
    _seed(db_session, "a2", "bob")  # other owner → walled out
    db_session.commit()

    resp = client.get(
        "/api/v1/sessions/export?format=eu-ai-act", headers=alice_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert '.eu-ai-act.json"' in resp.headers["content-disposition"]

    bundle = resp.json()
    # Offline signature verifies over the embedded canonical payload.
    assert dsse.verify_envelope(bundle) is True
    assert bundle["ed25519"]["public_key"]
    assert bundle["dsse"]["payloadType"] == dsse.DSSE_PAYLOAD_TYPE
    # The signed DSSE payload is the SOLE source of truth — the bundle carries
    # NO unsigned top-level statement copy (review-8d4a903c follow-up).
    assert "predicate" not in bundle
    signed = json.loads(base64.b64decode(bundle["dsse"]["payload"]))

    pred = signed["predicate"]
    assert pred["session_count"] == 1  # alice only, bob excluded
    s = pred["sessions"][0]
    assert s["session_id"] == "a1"
    ev = s["events"][0]
    assert ev["entry_hash"] == "h1"       # tamper-evidence chain surfaced
    assert ev["chain_version"] == 2
    assert ev["content_digest"] == "d1"
    assert pred["requirement_manifest"]["frameworks"]  # Art.12/19/26 + ISO + NIST


# ── M5: per-org compliance export  GET /orgs/{org_id}/audit-export ───────────
# The tenant wall (visible_scope_sync) is driven off the caller's profile in
# the data store; drive it with a fake store, same pattern as
# test_org_read_scope's unit matrix, so these router tests stay hermetic.
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


def _seed_org(db_session, sid, user, org_id, *, offset_sec=0):
    _seed(db_session, sid, user, offset_sec=offset_sec)
    db_session.commit()
    db_session.get(SessionRow, sid).org_id = org_id
    db_session.commit()


def _signed_pred(resp):
    bundle = resp.json()
    assert dsse.verify_envelope(bundle) is True
    assert "predicate" not in bundle          # signed payload = sole truth
    return json.loads(base64.b64decode(bundle["dsse"]["payload"]))["predicate"]


def test_org_audit_requires_auth(client):
    assert client.get("/api/v1/orgs/org-acme/audit-export").status_code == 401


def test_org_audit_member_exports_own_org(
    client, db_session, mint_token, monkeypatch
):
    """A member exports their own org: signed bundle, scoped to that org and
    to their within-org visibility (their own row in org-acme, not the
    other-org row)."""
    monkeypatch.setenv("YORU_SIGNING_KEY", _TEST_SEED_B64)
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    _seed_org(db_session, "o1", "dev@acme.dev", "org-acme", offset_sec=0)
    _seed_org(db_session, "o2", "dev@acme.dev", "org-other", offset_sec=10)

    resp = client.get("/api/v1/orgs/org-acme/audit-export", headers=headers)
    assert resp.status_code == 200, resp.text
    assert '.eu-ai-act.json"' in resp.headers["content-disposition"]
    assert "org-acme" in resp.headers["content-disposition"]
    pred = _signed_pred(resp)
    assert pred["session_count"] == 1
    assert pred["sessions"][0]["session_id"] == "o1"   # org-other walled out


def test_org_audit_member_cross_org_is_404(
    client, db_session, mint_token, monkeypatch
):
    """A member requesting an org outside their membership gets 404, not 403 —
    an invisible tenant must be indistinguishable from a nonexistent one."""
    monkeypatch.setenv("YORU_SIGNING_KEY", _TEST_SEED_B64)
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    _seed_org(db_session, "x1", "someone", "org-forbidden", offset_sec=0)

    resp = client.get("/api/v1/orgs/org-forbidden/audit-export", headers=headers)
    assert resp.status_code == 404


def test_org_audit_super_admin_exports_any_org(
    client, db_session, mint_token, monkeypatch
):
    """The studio super-admin may export any org, even one they aren't a member
    of, and is not walled by within-org email visibility."""
    monkeypatch.setenv("YORU_SIGNING_KEY", _TEST_SEED_B64)
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}],
    ))
    _raw, headers = mint_token("boss@studio.dev")
    _seed_org(db_session, "s1", "alice", "org-acme", offset_sec=0)
    _seed_org(db_session, "s2", "bob", "org-other", offset_sec=10)

    resp = client.get("/api/v1/orgs/org-acme/audit-export", headers=headers)
    assert resp.status_code == 200, resp.text
    pred = _signed_pred(resp)
    assert pred["session_count"] == 1
    assert pred["sessions"][0]["session_id"] == "s1"   # other org excluded


def test_org_audit_default_org_includes_null_org_rows(
    client, db_session, mint_token, monkeypatch
):
    """Exporting the default org sweeps legacy NULL-org_id rows (pre-M1)."""
    monkeypatch.setenv("YORU_SIGNING_KEY", _TEST_SEED_B64)
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u1", "email": "boss@studio.dev", "role": "admin"}],
    ))
    _raw, headers = mint_token("boss@studio.dev")
    _seed(db_session, "n1", "alice", offset_sec=0)          # org_id NULL
    _seed_org(db_session, "n2", "bob", "org-acme", offset_sec=10)
    db_session.commit()

    resp = client.get(
        f"/api/v1/orgs/{_V._DEFAULT_ORG_ID}/audit-export", headers=headers
    )
    assert resp.status_code == 200, resp.text
    pred = _signed_pred(resp)
    ids = {s["session_id"] for s in pred["sessions"]}
    assert ids == {"n1"}   # NULL-org row swept into default; org-acme excluded


def test_org_audit_requires_signing_key(
    client, db_session, mint_token, monkeypatch
):
    """No signing key → 409, even for a valid in-wall org (checked after the
    wall so a cross-org caller still gets 404, not a key hint)."""
    monkeypatch.delenv("YORU_SIGNING_KEY", raising=False)
    monkeypatch.setattr(_V, "get_data_store", lambda: _FakeStore(
        profiles=[{"id": "u2", "email": "dev@acme.dev", "role": "user"}],
        memberships=[{"user_id": "u2", "org_id": "org-acme"}],
    ))
    _raw, headers = mint_token("dev@acme.dev")
    _seed_org(db_session, "k1", "dev@acme.dev", "org-acme", offset_sec=0)

    resp = client.get("/api/v1/orgs/org-acme/audit-export", headers=headers)
    assert resp.status_code == 409
