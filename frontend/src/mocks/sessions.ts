import type {
  Filters,
  Session,
  SessionDetail,
  SessionEvent,
  SessionList,
  Summary,
} from "../types/receipt"

const now = Date.now()
const iso = (msAgo: number) => new Date(now - msAgo).toISOString()

const FIXTURES: Session[] = [
  {
    id: "s_01",
    user_email: "alice@acme.dev",
    agent: "claude-code",
    started_at: iso(12 * 60_000),
    ended_at: iso(3 * 60_000),
    duration_ms: 9 * 60_000 + 14_000,
    tool_count: 47,
    cost_usd: 0.84,
    flag_count: 2,
    flags: ["secret-pattern", "env-mutation"],
    tokens_input: 0,
    tokens_output: 0,
  },
  {
    id: "s_02",
    user_email: "bob@acme.dev",
    agent: "codex",
    started_at: iso(38 * 60_000),
    ended_at: iso(6 * 60_000),
    duration_ms: 32 * 60_000,
    tool_count: 128,
    cost_usd: 2.14,
    flag_count: 1,
    flags: ["migration-edit"],
    tokens_input: 0,
    tokens_output: 0,
  },
  {
    id: "s_03",
    user_email: "carol@acme.dev",
    agent: "opencode",
    started_at: iso(2 * 3600_000),
    ended_at: iso(2 * 3600_000 - 22 * 60_000),
    duration_ms: 22 * 60_000,
    tool_count: 53,
    cost_usd: 0.41,
    flag_count: 0,
    flags: [],
    tokens_input: 0,
    tokens_output: 0,
  },
  {
    id: "s_04",
    user_email: "alice@acme.dev",
    agent: "cursor",
    started_at: iso(5 * 60_000),
    ended_at: null,
    duration_ms: 5 * 60_000,
    tool_count: 18,
    cost_usd: 0.12,
    flag_count: 0,
    flags: [],
    tokens_input: 0,
    tokens_output: 0,
  },
  {
    id: "s_05",
    user_email: "dan@acme.dev",
    agent: "codex",
    started_at: iso(26 * 3600_000),
    ended_at: iso(26 * 3600_000 - 44 * 60_000),
    duration_ms: 44 * 60_000,
    tool_count: 201,
    cost_usd: 3.72,
    flag_count: 3,
    flags: ["shell-destructive", "ci-config-edit", "migration-edit"],
    tokens_input: 0,
    tokens_output: 0,
  },
  {
    id: "s_06",
    user_email: "bob@acme.dev",
    agent: "claude-code",
    started_at: iso(3 * 86400_000),
    ended_at: iso(3 * 86400_000 - 7 * 60_000),
    duration_ms: 7 * 60_000,
    tool_count: 22,
    cost_usd: 0.18,
    flag_count: 0,
    flags: [],
    tokens_input: 0,
    tokens_output: 0,
  },
  {
    id: "s_07",
    user_email: "alice@acme.dev",
    agent: "opencode",
    started_at: iso(4 * 86400_000),
    ended_at: iso(4 * 86400_000 - 18 * 60_000),
    duration_ms: 18 * 60_000,
    tool_count: 61,
    cost_usd: 0.63,
    flag_count: 1,
    flags: ["ci-config-edit"],
    tokens_input: 0,
    tokens_output: 0,
  },
  {
    id: "s_08",
    user_email: "eve@acme.dev",
    agent: "cursor",
    started_at: iso(6 * 86400_000),
    ended_at: iso(6 * 86400_000 - 11 * 60_000),
    duration_ms: 11 * 60_000,
    tool_count: 34,
    cost_usd: 0.28,
    flag_count: 0,
    flags: [],
    tokens_input: 0,
    tokens_output: 0,
  },
]

const EVENTS_BY_SESSION: Record<string, SessionEvent[]> = {
  s_01: [
    { id: "e1", session_id: "s_01", at: iso(12 * 60_000), type: "message", text: "Starting auth refactor." },
    { id: "e2", session_id: "s_01", at: iso(11 * 60_000 + 30_000), type: "tool_call", tool_name: "Read", tool_input: { path: "src/auth.py" } },
    { id: "e3", session_id: "s_01", at: iso(10 * 60_000), type: "file_change", file_path: "src/auth.py", file_op: "edit" },
    { id: "e4", session_id: "s_01", at: iso(9 * 60_000), type: "tool_call", tool_name: "Bash", tool_input: { cmd: "pytest tests/test_auth.py" } },
    { id: "e5", session_id: "s_01", at: iso(8 * 60_000), type: "error", error_message: "AssertionError: expected 200, got 401" },
    { id: "e6", session_id: "s_01", at: iso(7 * 60_000), type: "file_change", file_path: ".env.local", file_op: "edit", flag: "env-mutation" },
    { id: "e7", session_id: "s_01", at: iso(6 * 60_000), type: "tool_call", tool_name: "Write", tool_input: { path: "src/secrets.py", body: "API_KEY = 'sk_live_abcdef...'" }, flag: "secret-pattern" },
    { id: "e8", session_id: "s_01", at: iso(3 * 60_000), type: "message", text: "Tests passing. Done." },
  ],
  s_05: [
    { id: "f1", session_id: "s_05", at: iso(26 * 3600_000), type: "message", text: "Cleaning up old migrations + deploy." },
    { id: "f2", session_id: "s_05", at: iso(26 * 3600_000 - 2 * 60_000), type: "tool_call", tool_name: "Bash", tool_input: { cmd: "rm -rf migrations/old" }, flag: "shell-destructive" },
    { id: "f3", session_id: "s_05", at: iso(26 * 3600_000 - 10 * 60_000), type: "file_change", file_path: "migrations/006_add_sessions.py", file_op: "create", flag: "migration-edit" },
    { id: "f4", session_id: "s_05", at: iso(26 * 3600_000 - 20 * 60_000), type: "file_change", file_path: ".github/workflows/deploy.yml", file_op: "edit", flag: "ci-config-edit" },
    { id: "f5", session_id: "s_05", at: iso(26 * 3600_000 - 44 * 60_000), type: "message", text: "Shipped." },
  ],
}

const FILES_BY_SESSION: Record<string, SessionDetail["files_changed"]> = {
  s_01: [
    { path: "src/auth.py", op: "edit", additions: 12, deletions: 3 },
    { path: ".env.local", op: "edit", additions: 1, deletions: 1 },
    { path: "src/secrets.py", op: "create", additions: 8, deletions: 0 },
  ],
  s_05: [
    { path: "migrations/006_add_sessions.py", op: "create", additions: 40, deletions: 0 },
    { path: ".github/workflows/deploy.yml", op: "edit", additions: 6, deletions: 2 },
  ],
}

const SUMMARIES: Record<string, string> = {
  s_01: "Refactored `src/auth.py` to use bearer tokens; 8 tests green. Flagged a hardcoded API key in `src/secrets.py` and an `.env.local` write — review before commit.",
  s_02: "Generated migration 005 for the `receipts` table and wired it into alembic. No runtime failures. Migration review recommended before production rollout.",
  s_03: "Added pagination to the `/sessions` endpoint and backfilled tests. 22 min, 53 tool calls, clean exit. Nothing flagged.",
  s_04: "In progress — currently reading `package.json` and resolving a Tailwind PostCSS mismatch. No file writes yet.",
  s_05: "Cleaned up 4 legacy migrations and updated the deploy workflow. Flagged: `rm -rf migrations/old`, new migration file, CI config touched. High-impact session — audit before merge.",
  s_06: "Small bugfix: corrected off-by-one in the weekly-digest date window. 22 tool calls, no flags.",
  s_07: "Updated `.github/workflows/ci.yml` to add a frontend build step. CI config flag raised; review who authorized the change.",
  s_08: "Documentation pass — edited `README.md` and added a `CONTRIBUTING.md`. No code paths changed.",
}

function matches(s: Session, f: Filters): boolean {
  if (f.user && !s.user_email.toLowerCase().includes(f.user.toLowerCase())) return false
  if (f.flag_only && s.flag_count === 0) return false
  if (f.min_cost !== undefined && s.cost_usd < f.min_cost) return false
  if (f.date_from && s.started_at < f.date_from) return false
  if (f.date_to && s.started_at > f.date_to) return false
  return true
}

export async function mockListSessions(filters: Filters): Promise<SessionList> {
  await new Promise((r) => setTimeout(r, 150))
  const items = FIXTURES.filter((s) => matches(s, filters))
  return { items, total: items.length }
}

export async function mockGetSession(id: string): Promise<SessionDetail> {
  await new Promise((r) => setTimeout(r, 150))
  const base = FIXTURES.find((s) => s.id === id)
  if (!base) throw new Error(`session ${id} not found`)
  return {
    ...base,
    events: EVENTS_BY_SESSION[id] ?? [],
    files_changed: FILES_BY_SESSION[id] ?? [],
  }
}

export async function mockGetSummary(id: string): Promise<Summary> {
  await new Promise((r) => setTimeout(r, 300))
  return {
    text: SUMMARIES[id] ?? "No summary available yet.",
    model: "claude-haiku-4-5-20251001",
    cached_at: new Date().toISOString(),
  }
}
