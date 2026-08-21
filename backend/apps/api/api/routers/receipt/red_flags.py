"""Red-flag rule scanner for Receipt v0 events.

Rule ids are STABLE identifiers (never user-facing strings). Contract:
vault/BACKEND-API-V0.md §5. Patterns are compiled once at module load.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .skill_safety import severity_of_skill_rule

if TYPE_CHECKING:
    from .models import EventIn


# --- secret rules: scanned against content AND json.dumps(raw) ---
# Ordering is the output contract (scan_event returns in dict-iteration order).
# Append-only: new patterns go at the end so existing consumers see a stable
# prefix of IDs.
_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "secret_aws": re.compile(r"AKIA[0-9A-Z]{16}|aws_secret_access_key"),
    "secret_stripe": re.compile(r"sk_(?:live|test)_[0-9a-zA-Z]{24,}"),
    "secret_jwt": re.compile(
        r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    ),
    "secret_ssh_privkey": re.compile(
        r"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"
    ),
    # GitHub tokens: classic PATs (ghp_/gho_/ghs_/ghu_/ghr_ + 36 chars) and
    # fine-grained PATs (github_pat_ + 82 chars). Length anchors keep FP low.
    "secret_github": re.compile(
        r"gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{82}"
    ),
    "secret_google_api": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    # Slack bot/user/app/refresh/legacy tokens. Require a trailing body of
    # 10+ chars so bare `xoxb-` in docs doesn't trip.
    "secret_slack": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    # Database URL with inline credentials. Requires non-empty user AND
    # password before `@` — `postgres://localhost` and `postgres://user@host`
    # (no password) do not match.
    "secret_db_url": re.compile(
        r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^:\s/@]+:[^@\s/]+@"
    ),
    # OpenAI keys: legacy `sk-<48>` and project keys `sk-proj-<long>`. Uses
    # the dash form, so no collision with Stripe's underscore form.
    "secret_openai": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    # Anthropic keys: `sk-ant-api03-...` ~100 chars.
    "secret_anthropic": re.compile(r"sk-ant-[A-Za-z0-9_-]{90,}"),
    "secret_pgp": re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----"),
    # Yoru's OWN credentials: API keys (yoru_ak_) and hook/service tokens
    # (rcpt_, rcpt_u_, rcpt_s_, rcpt_reset_). An agent leaking the very key
    # that authenticates its receipts must be flagged like any other secret.
    # 20+ body chars so prose mentions of a bare prefix (or the
    # `rcpt_session` cookie name) don't trip.
    "secret_yoru_token": re.compile(
        r"(?:yoru_ak_|rcpt_(?:u_|s_|reset_)?)[A-Za-z0-9_-]{20,}"
    ),
}

# --- shell rules: tool_use + tool in {Bash, Shell} + content regex ---
# shell_rm: destructive file removal.
# shell_pipe_to_shell: curl/wget output piped into a shell interpreter — the
#   remote-code-execution pattern (e.g. `curl https://… | sh`).
# shell_git_force: force-push or hard reset — rewrites/discards history.
_SHELL_TOOLS: frozenset[str] = frozenset({"Bash", "Shell"})
_SHELL_RM_RE = re.compile(r"(?:^|[;&\|\s])rm\s+(?:-[rfRF]+\s+)?")
_SHELL_PIPE_TO_SHELL_RE = re.compile(
    r"\b(?:curl|wget|fetch)\b[^|;&]*\|\s*(?:sudo\s+)?(?:ba|z)?sh\b"
)
_SHELL_GIT_FORCE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgit\s+push\b[^|;&]*?(?:--force(?:-with-lease)?\b|\s-[A-Za-z]*f\b)"),
    re.compile(r"\bgit\s+reset\s+--hard\b"),
)

# --- db_destructive: tool_use on shell OR SQL clients + destructive SQL ---
# Triggers for raw SQL (sqlite3, psql, mysql) and direct statements in Bash.
# Flags: DROP / TRUNCATE / DELETE-without-WHERE / UPDATE-without-WHERE /
# DROP DATABASE. The "without WHERE" guard keeps scoped DELETE/UPDATE off the
# flag list — only unscoped whole-table mutations trip the wire.
# Added 2026-04-21 after a clean_db fixture wiped the live Receipt DB during
# an integration test run pointed at the production backend.
_DB_CLIENTS: frozenset[str] = frozenset({"Bash", "Shell", "sqlite3", "psql", "mysql"})
_DB_DESTRUCTIVE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?\w+", re.IGNORECASE),
    # DELETE FROM <tbl> — anchor the negative lookahead at the position right
    # after the table name so the engine evaluates "is there a WHERE before
    # the next ; anywhere in the rest of the statement?" exactly once. A lazy
    # body after SET allowed the engine to shrink the match until WHERE fell
    # out of the lookahead window — false-negative — so we use a fixed anchor.
    re.compile(r"\bDELETE\s+FROM\s+\w+\b(?![^;]*\bWHERE\b)", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+\w+\s+SET\b(?![^;]*\bWHERE\b)", re.IGNORECASE),
)

# --- path rules: triggered only when kind == "file_change" ---
_MIGRATION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:^|/)migrations/"),
    re.compile(r"(?:^|/)alembic/versions/"),
    re.compile(r".*\.sql$"),
)
_ENV_MUTATION_RE = re.compile(r"(?:^|/)\.env(?:\..*)?$")
_CI_CONFIG_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.github/workflows/.*\.ya?ml$"),
    re.compile(r"^\.gitlab-ci\.ya?ml$"),
    re.compile(r"^\.circleci/config\.ya?ml$"),
    re.compile(r"^Dockerfile$"),
    re.compile(r"^docker-compose\.ya?ml$"),
)


# ── six-kind taxonomy + severity (S2, design trovex:28547568 §6) ─────────────
# The canonical rule_id -> user-facing category map. Six kinds, load-bearing
# (mirrored by the dashboard's normalizeFlag + marketing). This is the backend
# source of truth; anything consuming rule_ids maps THROUGH here so the two
# lists never drift. Do NOT add a seventh kind here — the brief's velocity /
# prompt-injection / scope / failure-masking rules are a SEPARATE future
# ruleset, not folded into the six.
_CATEGORY_SEVERITY: dict[str, str] = {
    "secret": "critical",
    "db": "critical",
    "shell": "high",
    "ci": "high",
    "env": "medium",
    "migration": "medium",
}


def category_of(rule_id: str) -> str:
    """Map a stable rule_id to one of the six user-facing categories.

    secret_* -> secret; shell_* -> shell; db_destructive -> db;
    migration_file -> migration; env_mutation -> env; ci_config -> ci.
    ``custom:*`` -> "custom" — an additive 7th value for org-defined rules
    (design trovex:961a5e80, task 569f1d47). ``skill:*`` -> "skill-safety" —
    an additive 8th value for the built-in skill-content ruleset (design cto
    2026-08-21, task fa3baa27). Do NOT add a preset here for either; the six
    stay exactly six. An unrecognized id falls back to "secret" only if it is
    secret-prefixed, else "shell" is never assumed — unknown ids return
    "other" so a taxonomy drift surfaces instead of silently miscategorizing.
    """
    if rule_id.startswith("secret_"):
        return "secret"
    if rule_id.startswith("shell_"):
        return "shell"
    if rule_id == "db_destructive":
        return "db"
    if rule_id == "migration_file":
        return "migration"
    if rule_id == "env_mutation":
        return "env"
    if rule_id == "ci_config":
        return "ci"
    if rule_id.startswith("custom:"):
        return "custom"
    if rule_id.startswith("skill:"):
        return "skill-safety"
    return "other"


def severity_of(rule_id: str, custom_severity: str | None = None) -> str:
    """Coarse audit severity (critical | high | medium) for a rule_id, derived
    from its category. Unknown categories default to "high" so an
    unclassified signal is never quietly downgraded.

    A custom rule's severity is user-configured, not derivable from its
    category — the caller (events_router, which already has the compiled
    rule in hand at scan time) passes it via ``custom_severity``. Falls back
    to "high" if a custom rule_id shows up without one (defensive — should
    never happen on the real ingest path).

    A skill-safety rule's severity is embedded in its own static catalog
    (``skill_safety._RULES``) rather than caller-supplied — it's a built-in
    deterministic table, not per-org configurable like custom rules.
    """
    if rule_id.startswith("custom:"):
        return custom_severity or "high"
    if rule_id.startswith("skill:"):
        return severity_of_skill_rule(rule_id)
    return _CATEGORY_SEVERITY.get(category_of(rule_id), "high")


def scan_event(e: "EventIn") -> list[str]:
    """Return a deduped list of rule ids triggered by ``e``.

    Order is stable: secret_* first (in declaration order), then shell_rm,
    shell_pipe_to_shell, shell_git_force, db_destructive, then migration_file,
    env_mutation, ci_config.
    """
    flags: list[str] = []
    seen: set[str] = set()

    def _add(rule_id: str) -> None:
        if rule_id not in seen:
            seen.add(rule_id)
            flags.append(rule_id)

    content = e.content or ""
    # Scan everything in `raw` EXCEPT `tool_response` — reading a file that
    # mentions a secret pattern is not exfiltration, it's discovery. That's
    # the only silently-skipped data source; the scanner is otherwise strict.
    # Judging "test fixture vs. real leak" is the reviewer's job, not the
    # scanner's — skipping scans based on path would be the scanner lying
    # about what it found, which defeats audit-grade discipline.
    raw = e.raw if isinstance(e.raw, dict) else {}
    raw_minus_response = {k: v for k, v in raw.items() if k != "tool_response"}
    raw_blob = json.dumps(raw_minus_response) if raw_minus_response else ""

    for rule_id, pattern in _SECRET_PATTERNS.items():
        if pattern.search(content) or (raw_blob and pattern.search(raw_blob)):
            _add(rule_id)

    if (
        e.kind == "tool_use"
        and e.tool in _SHELL_TOOLS
        and content
    ):
        if _SHELL_RM_RE.search(content):
            _add("shell_rm")
        if _SHELL_PIPE_TO_SHELL_RE.search(content):
            _add("shell_pipe_to_shell")
        if any(p.search(content) for p in _SHELL_GIT_FORCE_RES):
            _add("shell_git_force")

    if (
        e.kind == "tool_use"
        and e.tool in _DB_CLIENTS
        and content
        and any(p.search(content) for p in _DB_DESTRUCTIVE_RES)
    ):
        _add("db_destructive")

    if e.kind == "file_change" and e.path:
        path = e.path
        if any(p.search(path) for p in _MIGRATION_RES):
            _add("migration_file")
        if _ENV_MUTATION_RE.search(path):
            _add("env_mutation")
        if any(p.search(path) for p in _CI_CONFIG_RES):
            _add("ci_config")

    return flags
