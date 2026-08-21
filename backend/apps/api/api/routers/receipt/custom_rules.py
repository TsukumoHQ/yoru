"""Org-defined custom red-flag rules (design trovex:961a5e80, task 569f1d47).

Generalizes the six built-in presets (``red_flags.py``) into a per-org
user-editable ruleset. Kept as a SEPARATE module from ``red_flags.py`` — the
preset scanner stays pure/DB-free (its existing contract); this module owns
the one thing presets don't need: loading + caching an org's rule set and
matching it against an event.

v1 match types are deliberately linear-time only: ``contains`` (plain
substring) and ``path_glob`` (fnmatch). ``regex`` is rejected at create time
(cto ruling 2026-08-21: user-authored regex on shared ingest infra is a real
ReDoS ticket, not a v1 corner-cut — fast-follow gated on a linear-time engine).
"""
from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlmodel import select

if TYPE_CHECKING:
    from sqlmodel import Session as SQLSession

    from .models import EventIn

MATCH_TYPES: tuple[str, ...] = ("contains", "path_glob")
SEVERITIES: tuple[str, ...] = ("critical", "high", "medium")
MAX_PATTERN_LENGTH = 512
MAX_RULES_PER_ORG = 50
_CACHE_TTL_SECONDS = 30.0


class InvalidRule(ValueError):
    """A rule fails v1 validation (bad match_type/severity/pattern, or the
    org is at its rule cap). Routers translate this to a 400."""


def validate_rule(
    *,
    org_id: str,
    match_type: str,
    pattern: str,
    severity: str,
    session: "SQLSession",
    enforce_cap: bool = True,
) -> None:
    """Raises InvalidRule on a bad match_type/severity/pattern, or (create
    path only — ``enforce_cap=True``) an org already at ``MAX_RULES_PER_ORG``.
    The update path passes ``enforce_cap=False``: editing an existing rule
    must not be blocked by the org's own count including that same row."""
    if match_type not in MATCH_TYPES:
        raise InvalidRule(
            f"match_type must be one of {MATCH_TYPES} — regex is deferred "
            "(ReDoS risk on shared ingest infra), not supported in v1"
        )
    if severity not in SEVERITIES:
        raise InvalidRule(f"severity must be one of {SEVERITIES}")
    if not pattern or len(pattern) > MAX_PATTERN_LENGTH:
        raise InvalidRule(f"pattern must be 1..{MAX_PATTERN_LENGTH} chars")
    if enforce_cap:
        from .models import CustomRule
        count = len(list(session.exec(
            select(CustomRule.id).where(CustomRule.org_id == org_id)
        ).all()))
        if count >= MAX_RULES_PER_ORG:
            raise InvalidRule(f"org already has {MAX_RULES_PER_ORG} custom rules (the cap)")


@dataclass(frozen=True)
class CompiledRule:
    """Immutable, DB-free projection of a CustomRule row — what the hot
    ingest path actually needs to test an event, cached per org."""
    id: str
    kind_filter: str | None
    tool_filter: tuple[str, ...] | None
    match_type: str
    pattern: str
    severity: str

    def matches(self, e: "EventIn") -> bool:
        if self.kind_filter and e.kind != self.kind_filter:
            return False
        if self.tool_filter and e.tool not in self.tool_filter:
            return False
        if self.match_type == "contains":
            return bool(e.content) and self.pattern in e.content
        if self.match_type == "path_glob":
            return bool(e.path) and fnmatch.fnmatch(e.path, self.pattern)
        return False  # unreachable given validate_rule, but never crash the ingest path


def scan_custom(e: "EventIn", rules: list[CompiledRule]) -> list[tuple[str, str]]:
    """Return [(namespaced_rule_id, severity), ...] for every custom rule
    ``e`` trips. Namespacing (``custom:<id>``) keeps a custom hit from ever
    colliding with or being mistaken for a preset rule_id downstream."""
    return [(f"custom:{r.id}", r.severity) for r in rules if r.matches(e)]


# ── per-org TTL cache, eager invalidation on CRUD ────────────────────────────
# Avoids a DB round-trip per ingested event (the hot PostToolUse path). A
# module-level dict is fine here — same-process, same lifetime as every other
# in-process cache in this codebase (e.g. the LiteLLM pricing cache).
_cache: dict[str, tuple[float, list[CompiledRule]]] = {}


def get_org_rules(session: "SQLSession", org_id: str | None) -> list[CompiledRule]:
    if not org_id:
        return []
    now = time.monotonic()
    cached = _cache.get(org_id)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    from .models import CustomRule
    rows = session.exec(
        select(CustomRule).where(
            CustomRule.org_id == org_id, CustomRule.enabled == True  # noqa: E712
        )
    ).all()
    compiled = [
        CompiledRule(
            id=r.id,
            kind_filter=r.kind_filter,
            tool_filter=tuple(r.tool_filter) if r.tool_filter else None,
            match_type=r.match_type,
            pattern=r.pattern,
            severity=r.severity,
        )
        for r in rows
    ]
    _cache[org_id] = (now, compiled)
    return compiled


def invalidate_org_cache(org_id: str) -> None:
    """Called on every rule create/update/delete so an edit takes effect on
    the next event without waiting out the TTL or a process restart."""
    _cache.pop(org_id, None)
