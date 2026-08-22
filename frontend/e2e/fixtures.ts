import type { Page } from "@playwright/test";

export const SESSION_ID = "sess-e2e-fixture-001";

const ME = { user: { id: "user-1", email: "dev@example.com", is_super_admin: false } };

const ORGANIZATIONS = {
  items: [{ id: "org-1", name: "Acme Corp", slug: "acme", type: "team" }],
};

const TOKEN_ANALYTICS = {
  bucket: "day",
  since: "2026-08-15",
  until: "2026-08-22",
  own: {
    totals: { tokens_input: 120000, tokens_output: 45000, cost_usd: 12.5 },
    series: [
      { bucket_start: "2026-08-20", tokens_input: 40000, tokens_output: 15000, cost_usd: 4.0 },
      { bucket_start: "2026-08-21", tokens_input: 80000, tokens_output: 30000, cost_usd: 8.5 },
    ],
  },
  org: {
    totals: { tokens_input: 900000, tokens_output: 300000, cost_usd: 95.0 },
    series: [
      { bucket_start: "2026-08-20", tokens_input: 300000, tokens_output: 100000, cost_usd: 30.0 },
      { bucket_start: "2026-08-21", tokens_input: 600000, tokens_output: 200000, cost_usd: 65.0 },
    ],
    top_spenders: [
      { user: "dev@example.com", tokens_input: 120000, tokens_output: 45000, cost_usd: 12.5 },
    ],
    by_dev: [
      { user: "dev@example.com", tokens_input: 120000, tokens_output: 45000, cost_usd: 12.5 },
      { user: "other@example.com", tokens_input: 80000, tokens_output: 30000, cost_usd: 8.0 },
    ],
    by_project: [{ vcs: "github", tokens_input: 500000, tokens_output: 150000, cost_usd: 50.0 }],
  },
};

const SESSION_DETAIL = {
  id: SESSION_ID,
  user: "dev@example.com",
  started_at: "2026-08-21T10:00:00Z",
  ended_at: "2026-08-21T10:30:00Z",
  tools_count: 3,
  cost_usd: 1.23,
  tokens_input: 5000,
  tokens_output: 2000,
  flags: ["shell_rm"],
  title: "Fix auth middleware bug",
  workspace_id: "ws-1",
  org_id: "org-1",
  is_public: false,
  grade: "B",
  vcs: "github",
  events: [
    {
      id: "evt-1",
      ts: "2026-08-21T10:01:00Z",
      kind: "tool_use",
      tool: "Bash",
      path: null,
      content: null,
      output: "ok",
      flags: [],
      duration_ms: 120,
      group_key: "g1",
      tool_input: { command: "ls" },
      cost_usd: 0.01,
      tokens_input: 50,
      tokens_output: 20,
    },
    {
      id: "evt-2",
      ts: "2026-08-21T10:05:00Z",
      kind: "file_change",
      tool: "Edit",
      path: "src/auth.py",
      content: "diff content",
      output: null,
      flags: [],
      duration_ms: 200,
      group_key: "g2",
      tool_input: { file_path: "src/auth.py" },
      cost_usd: 0.02,
      tokens_input: 80,
      tokens_output: 40,
    },
    {
      id: "evt-3",
      ts: "2026-08-21T10:10:00Z",
      kind: "error",
      tool: "Bash",
      path: null,
      content: null,
      output: "command failed",
      flags: ["shell_rm"],
      duration_ms: 50,
      group_key: "g3",
      tool_input: { command: "rm -rf /tmp/x" },
      cost_usd: 0.0,
      tokens_input: 10,
      tokens_output: 5,
    },
  ],
  files_changed: [{ path: "src/auth.py", op: "edit", additions: 12, deletions: 3 }],
  summary: null,
  score: {
    overall: 82,
    throughput: 90,
    reliability: 75,
    safety: 80,
    grade: "B",
    breakdown: {},
  },
  agent_confidence: "declared",
  enforce_available: true,
};

const SESSION_SUMMARY = {
  text: "Fixed an off-by-one in the auth token expiry check and added a regression test.",
  model: "claude-sonnet-5",
  cached_at: "2026-08-21T10:31:00Z",
};

export async function mockApi(page: Page) {
  await page.route("**/api/v1/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/v1/auth/session/me") {
      return route.fulfill({ json: ME });
    }
    if (path === "/api/v1/me/organizations") {
      return route.fulfill({ json: ORGANIZATIONS });
    }
    if (path === "/api/v1/analytics/tokens") {
      return route.fulfill({ json: TOKEN_ANALYTICS });
    }
    if (path === `/api/v1/sessions/${SESSION_ID}/summary`) {
      return route.fulfill({ json: SESSION_SUMMARY });
    }
    if (path === `/api/v1/sessions/${SESSION_ID}`) {
      return route.fulfill({ json: SESSION_DETAIL });
    }
    if (path === "/api/v1/orgs/org-1/red-flag-rules") {
      return route.fulfill({ json: [] });
    }
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
}
