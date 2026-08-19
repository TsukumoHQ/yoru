"""EU-AI-Act audit-export projection (M5b, design 44a3774a §6 / steal #1).

Builds the in-toto *statement* that the DSSE signer wraps: the org-scoped
sessions with their tamper-evidence chain fields surfaced (entry_hash /
prev_hash / chain_version / content_digest), plus a requirement manifest that
maps the bundle's fields to the record-keeping obligations of the EU AI Act,
ISO/IEC 42001, and the NIST AI RMF.

The manifest states which fields serve as EVIDENCE for each obligation; it does
NOT assert that a deployment is compliant — compliance is the deployer's
determination. Wording is deliberately factual (no certification claims).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import Event, Session as SessionRow

# Static field→obligation mapping. Additive: extend, don't renumber.
REQUIREMENT_MANIFEST = {
    "frameworks": ["EU AI Act (Reg. 2024/1689)", "ISO/IEC 42001", "NIST AI RMF 1.0"],
    "disclaimer": (
        "This manifest lists the bundle fields that serve as record-keeping "
        "evidence for each obligation. It does not certify compliance; that "
        "determination is the deployer's."
    ),
    "mappings": [
        {
            "requirement": "EU AI Act Art. 12 — record-keeping (automatic event logging over the system lifetime)",
            "evidence_fields": [
                "sessions[].events[].ts",
                "sessions[].events[].kind",
                "sessions[].events[].entry_hash",
                "sessions[].events[].prev_hash",
            ],
            "note": "Every agent action is logged with a chained, tamper-evident hash.",
        },
        {
            "requirement": "EU AI Act Art. 19 — automatically generated logs kept for the appropriate period",
            "evidence_fields": ["sessions[]", "generated_at", "dsse"],
            "note": "Logs are retained by the deployer and their integrity is signed.",
        },
        {
            "requirement": "EU AI Act Art. 26 — deployer obligation to keep logs under the deployer's control",
            "evidence_fields": ["org_scope", "sessions[].org_id", "ed25519.public_key"],
            "note": "Self-hosted, org-scoped trail signed by the deployer's own key.",
        },
        {
            "requirement": "ISO/IEC 42001 — AI management system operational logging & traceability",
            "evidence_fields": [
                "sessions[].events[].content_digest",
                "sessions[].events[].chain_version",
                "dsse.signatures",
            ],
            "note": "Per-event content commitments plus a signed envelope give end-to-end traceability.",
        },
        {
            "requirement": "NIST AI RMF — MEASURE/MANAGE: traceability & accountability of AI system behaviour",
            "evidence_fields": ["sessions[].flags", "sessions[].flagged", "sessions[].events[].flags"],
            "note": "Red-flag taxonomy (secret/env/shell/db/migration/ci) records risk-relevant actions.",
        },
    ],
}


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return None if dt is None else dt.isoformat()


def eu_ai_act_session(row: SessionRow, events: list[Event]) -> dict:
    """One session with its chain fields surfaced (the tamper-evidence the
    plain json/csv exports omit)."""
    return {
        "session_id": row.id,
        "org_id": getattr(row, "org_id", None),
        "user_email": row.user,
        "started_at": _iso(row.started_at),
        "ended_at": _iso(row.ended_at),
        "tools_count": row.tools_count,
        "files_count": row.files_count,
        "cost_usd": row.cost_usd,
        "flagged": row.flagged,
        "flags": row.flags or [],
        "summary": row.summary,
        "events": [
            {
                "ts": _iso(e.ts),
                "kind": e.kind,
                "tool": e.tool,
                "path": e.path,
                "flags": e.flags or [],
                # Tamper-evidence chain (the whole point of the eu-ai-act bundle).
                "entry_hash": e.entry_hash,
                "prev_hash": e.prev_hash,
                "chain_version": e.chain_version,
                "content_digest": e.content_digest,
            }
            for e in events
        ],
    }


def build_statement(
    sessions: list[dict],
    *,
    org_scope,
    truncated: bool,
    export_cap: int,
    predicate_type: str,
    generated_at: Optional[datetime] = None,
) -> dict:
    """Assemble the in-toto statement the DSSE envelope signs. ``org_scope`` is
    a sorted list of org ids, or None for a super-admin all-orgs export."""
    gen = generated_at or datetime.now(timezone.utc)
    stamp = gen.strftime("%Y%m%dT%H%M%SZ")
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "predicateType": predicate_type,
        "subject": [{"name": f"yoru-audit-export-{stamp}"}],
        "predicate": {
            "generated_at": gen.isoformat(),
            "org_scope": "all" if org_scope is None else sorted(org_scope),
            "session_count": len(sessions),
            "truncated": truncated,
            "export_cap": export_cap,
            "sessions": sessions,
            "requirement_manifest": REQUIREMENT_MANIFEST,
        },
    }
