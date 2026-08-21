"""Skill-safety red-flag rules (design cto 2026-08-21, task fa3baa27).

Detects malicious SKILL content (prompt injection, exfil, reverse-shell,
secrets) transiting the audited event stream — the "steal deer-flow
SkillScan" ruleset, ported to yoru's deterministic red-flag engine instead
of an LLM judge (source: deer-flow skills/skillscan/orchestrator.py, MIT;
Snyk ToxicSkills found 36% of public skills carry prompt injection).

Kept as a SEPARATE module from ``red_flags.py`` — same reasoning as
``custom_rules.py``: the preset scanner stays pure/DB-free, this module owns
the one thing presets don't need: a skill-scoped rule table. Unlike
``custom_rules``, this is a BUILT-IN deterministic catalog (16 rules, no
LLM, no per-org customization) — no DB table, no migration, no cache.

Scope: only evaluated when the event touches skill surface — a SKILL.md
file, a ``.claude/skills/`` path, or a Claude settings.json (hooks live
there). Everything else short-circuits to zero cost. Within that scope, the
combined path+content+raw blob is scanned (including tool_response, unlike
``red_flags.scan_event``'s secret patterns) — discovering that a skill ALREADY
carries malicious content is exactly the signal this ruleset exists to
surface, not something to suppress as "mere reading".

All 16 patterns are linear-time (bounded quantifiers only, no nested
unbounded repetition) — same ReDoS discipline as ``custom_rules`` (cto
ruling 2026-08-21), doubly important here since these run against
attacker-controlled skill content by design.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import EventIn


# ── skill-surface scoping ────────────────────────────────────────────────────
# An event is "in skill context" when its path or content references a skill
# file. Everything below only runs for events that pass this gate.
_SKILL_PATH_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|/)SKILL\.md$"),
    re.compile(r"(?:^|/)\.claude/skills/"),
    re.compile(r"(?:^|/)\.claude/settings(?:\.local)?\.json$"),
    re.compile(r"(?:^|/)hooks/settings\.json$"),
)


def _is_skill_context(e: "EventIn") -> bool:
    if e.path and any(p.search(e.path) for p in _SKILL_PATH_RES):
        return True
    content = e.content or ""
    return bool(content) and any(p.search(content) for p in _SKILL_PATH_RES)


# ── 16-rule catalog: id -> (pattern, severity) ──────────────────────────────
# rule_id is namespaced ``skill:<id>`` by scan_skill_safety() below so it can
# never collide with a preset or a custom:<id> rule_id.
_EXFIL_SENSITIVE_PATH_RE = re.compile(
    r"~/\.ssh|~/\.aws|~/\.config/gh|/etc/passwd|/etc/shadow"
)
_EXFIL_NETWORK_RE = re.compile(
    r"\bcurl\b[^\n]*(?:-d\b|--data(?:-binary)?\b|-F\b)|\bwget\b[^\n]*--post-data"
)

_RULES: dict[str, tuple[re.Pattern[str] | None, str]] = {
    "shell-curl-pipe-shell": (
        re.compile(r"\b(?:curl|wget|fetch)\b[^\n|;&]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"),
        "high",
    ),
    "shell-reverse-shell": (
        re.compile(
            r"bash\s+-i\s*>&\s*/dev/tcp/"
            r"|\bnc\b[^\n]*-e\b"
            r"|\bmkfifo\b[^\n|]*\|\s*nc\b"
        ),
        "critical",
    ),
    # Both a sensitive-path read AND a network exfil primitive must appear in
    # the same blob — two bounded .search() calls, still linear time.
    "shell-sensitive-exfil": (
        None,  # handled specially in scan_skill_safety (needs an AND, not OR)
        "critical",
    ),
    "shell-destructive": (
        re.compile(
            r"rm\s+-[rRfF]{1,2}\s+(?:/|~)(?:\s|$)"
            r"|\bmkfs\.\w+"
            r"|\bdd\s+[^\n]*of=/dev/"
        ),
        "critical",
    ),
    "shell-env-dump": (
        re.compile(
            r"\bprintenv\b"
            r"|\benv\s*(?:\||>)"
            r"|\bexport\s+-p\b"
            r"|\bcat\s+/proc/self/environ\b"
        ),
        "medium",
    ),
    "path-credentials": (
        re.compile(
            r"~/\.ssh(?:/|\b)"
            r"|~/\.aws(?:/|\b)"
            r"|~/\.config/gh(?:/|\b)"
            r"|\.netrc\b"
            r"|\.npmrc\b"
            r"|/etc/shadow\b"
            r"|docker\.sock\b"
        ),
        "high",
    ),
    "net-cloud-metadata": (
        re.compile(r"169\.254\.169\.254|metadata\.google\.internal"),
        "high",
    ),
    "net-raw-ip-endpoint": (
        re.compile(r"https?://(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?"),
        "medium",
    ),
    "inject-override": (
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:all\s+)?"
            r"(?:previous|prior|above)\s+(?:instructions|rules)\b",
            re.IGNORECASE,
        ),
        "critical",
    ),
    "inject-role-escape": (
        re.compile(
            r"\byou are now\b"
            r"|\bact as (?:if you (?:are|were)\s+)?(?:system|root|admin)\b"
            r"|\bnew system prompt\b"
            r"|\bDAN mode\b",
            re.IGNORECASE,
        ),
        "high",
    ),
    "inject-concealment": (
        re.compile(
            r"\bdo not\s+(?:tell|mention|reveal|inform)\b[^\n.]{0,60}"
            r"\b(?:user|human)\b",
            re.IGNORECASE,
        ),
        "critical",
    ),
    "inject-invisible-text": (
        # Zero-width space/joiners/marks (U+200B-200F), bidi overrides
        # (U+202A-202E), word joiner/invisible operators (U+2060-2064), BOM
        # (U+FEFF), and the Unicode tag block (U+E0000-E007F) — the
        # steganographic prompt-injection carrier. \u/\U escapes only — no
        # literal invisible chars in source (unreadable + unverifiable in
        # review).
        re.compile(
            "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff"
            "\U000e0000-\U000e007f]"
        ),
        "high",
    ),
    # Overlaps red_flags.py's secret_ssh_privkey/secret_pgp/secret_aws by
    # design: those fire under category "secret" regardless of context, these
    # fire under category "skill-safety" specifically because the leak sits
    # inside skill content — a distinct audit signal, not a duplicate one.
    "secret-private-key": (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "critical",
    ),
    "secret-token": (
        re.compile(
            r"AKIA[0-9A-Z]{16}"
            r"|ghp_[A-Za-z0-9]{20,}"
            r"|sk-ant-[A-Za-z0-9_-]{10,}"
            r"|xox[bp]-[A-Za-z0-9-]{10,}"
        ),
        "critical",
    ),
    "hook-secret-touch": (
        re.compile(
            r'"hooks"[\s\S]{0,200}?'
            r"(?:curl|wget|~/\.ssh|~/\.aws|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})"
        ),
        "high",
    ),
    "pkg-structure": (
        re.compile(r"(?:^|/)\.\.(?:/|$)|\\\.\.(?:\\|$)"),
        "medium",
    ),
}

assert len(_RULES) == 16, "skill-safety catalog must stay at exactly 16 v1 rules"


def scan_skill_safety(e: "EventIn") -> list[str]:
    """Return deduped ``skill:<id>`` rule ids triggered by ``e``.

    No-ops (zero regex work) unless ``e`` is in skill context. Within
    context, scans path + content + raw (tool_response included — see
    module docstring) as one blob, then each of the 16 rules independently.
    """
    if not _is_skill_context(e):
        return []

    parts: list[str] = []
    if e.path:
        parts.append(e.path)
    if e.content:
        parts.append(e.content)
    raw = e.raw if isinstance(e.raw, dict) else {}
    if raw:
        parts.append(json.dumps(raw))
    blob = "\n".join(parts)
    if not blob:
        return []

    hits: list[str] = []
    for rule_id, (pattern, _sev) in _RULES.items():
        if rule_id == "shell-sensitive-exfil":
            if _EXFIL_SENSITIVE_PATH_RE.search(blob) and _EXFIL_NETWORK_RE.search(blob):
                hits.append(f"skill:{rule_id}")
            continue
        if pattern is not None and pattern.search(blob):
            hits.append(f"skill:{rule_id}")
    return hits


def severity_of_skill_rule(rule_id: str) -> str:
    """Severity for a ``skill:<id>`` rule_id (red_flags.severity_of delegates
    here). Falls back to "high" for an id outside the table — defensive,
    should never happen on the real scan_skill_safety path."""
    base_id = rule_id.split(":", 1)[1] if rule_id.startswith("skill:") else rule_id
    entry = _RULES.get(base_id)
    return entry[1] if entry else "high"
