import type {
  Filters,
  SessionDetail,
  SessionList,
  Summary,
} from "../types/receipt"
import { mockListSessions, mockGetSession, mockGetSummary } from "../mocks/sessions"
import { queryClient } from "./queryClient"

const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "1"
const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8002/api/v1"

export class ApiError extends Error {
  constructor(public status: number, public body: string) {
    super(`API ${status}: ${body}`)
  }
}

// Billing plan lives here (not further down) because the 402 interceptor
// below must prime the shared ['orgs','me'] cache shape.
export type Plan = "free" | "pro" | "team" | "org"

// Shared cache shape for ['orgs','me'] — single source of truth for the
// authenticated org's plan + quota state (US-V4-1 AC #6). Siblings
// (post-upgrade poll, plan badge) can widen as needed.
export interface OrgsMe {
  plan: Plan
  quota_exceeded: boolean
}

export const ORGS_ME_KEY = ["orgs", "me"] as const

let tokenExpiredHandled = false

// Cookie-based auth. The browser auto-attaches `rcpt_session` (HttpOnly) on
// every request thanks to `credentials: 'include'`. For mutating methods we
// echo the `rcpt_csrf` cookie (readable) as `X-CSRF-Token` — double-submit
// CSRF protection per backend/apps/api/api/middleware/csrf.py.
function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : null
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase()
  const headers = new Headers(init?.headers)
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json")
  }
  const mutating = method !== "GET" && method !== "HEAD" && method !== "OPTIONS"
  if (mutating) {
    const csrf = readCookie("rcpt_csrf")
    if (csrf) headers.set("X-CSRF-Token", csrf)
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  })
  const text = await res.text()
  if (!res.ok) {
    if (res.status === 401 && !tokenExpiredHandled) {
      // Cookie expired or invalid — bounce to signin. The backend already
      // rotates refresh automatically; if we're still 401 here, refresh also
      // failed and the user must re-authenticate.
      tokenExpiredHandled = true
      window.location.assign("/signin?reason=token-expired")
    }
    if (res.status === 402) {
      // Quota paywall — prime the ['orgs','me'] cache so <UpgradeBanner/>
      // renders without waiting for a /orgs/me refetch (US-V4-1 AC #6:
      // single source of truth).
      queryClient.setQueryData<OrgsMe | undefined>(
        ORGS_ME_KEY,
        (prev) => ({ plan: "free", ...prev, quota_exceeded: true }),
      )
    }
    throw new ApiError(res.status, text)
  }
  return text ? (JSON.parse(text) as T) : (undefined as T)
}

function qs(f: Filters): string {
  const p = new URLSearchParams()
  if (f.user) p.set("user", f.user)
  if (f.date_from) p.set("from_ts", f.date_from)
  if (f.date_to) p.set("to_ts", f.date_to)
  if (f.flag_only) p.set("flagged", "true")
  if (f.min_cost !== undefined) p.set("min_cost", String(f.min_cost))
  if (f.workspace_id) p.set("workspace_id", f.workspace_id)
  if (f.limit !== undefined) p.set("limit", String(f.limit))
  if (f.offset !== undefined) p.set("offset", String(f.offset))
  return p.toString()
}

// Backend returns `user`/`tools_count`/`ended_at` — frontend types use
// `user_email`/`tool_count`/`duration_ms`. Map on the way out.
interface RawSession {
  id: string
  user: string
  started_at: string
  ended_at: string | null
  tools_count: number
  cost_usd: number
  tokens_input?: number
  tokens_output?: number
  flags: string[]
  title?: string | null
  workspace_id?: string | null
  is_public?: boolean
  grade?: string | null
}

// Backend rule_id (snake_case, per red_flags.py) → frontend RedFlagKind (kebab).
// Secrets collapse into one user-facing category since the sub-flavour
// (aws/stripe/github/…) is an implementation detail of the scanner. Shell
// rules (rm / pipe-to-shell / git --force / reset --hard) collapse into one
// "destructive" bucket so the backend can grow the family without a frontend
// deploy. Unknown ids are filtered out (we never render broken badges) but
// we warn in the console so frontend drift after a backend rule addition is
// visible during development — see conversation on 2026-04-22.
const WARNED_UNKNOWN = new Set<string>()
function normalizeFlag(rule: string): import("../types/receipt").RedFlagKind | null {
  if (rule.startsWith("secret_")) return "secret-pattern"
  switch (rule) {
    case "shell_rm":
    case "shell_pipe_to_shell":
    case "shell_git_force":
      return "shell-destructive"
    case "db_destructive": return "db-destructive"
    case "migration_file": return "migration-edit"
    case "env_mutation":   return "env-mutation"
    case "ci_config":      return "ci-config-edit"
    default:
      if (!WARNED_UNKNOWN.has(rule)) {
        WARNED_UNKNOWN.add(rule)
        console.warn(`[red-flag] unknown backend rule_id %o — dropped from UI. Update normalizeFlag in lib/api.ts.`, rule)
      }
      return null
  }
}

function normalizeFlags(rules: string[] | undefined): import("../types/receipt").RedFlagKind[] {
  const out: import("../types/receipt").RedFlagKind[] = []
  const seen = new Set<string>()
  for (const r of rules ?? []) {
    const k = normalizeFlag(r)
    if (k && !seen.has(k)) {
      seen.add(k)
      out.push(k)
    }
  }
  return out
}

// Collapse a raw {rule_id: count} map (e.g. SessionTotals.flags_by_kind) into
// counts keyed by user-facing RedFlagKind. Used by the Fleet-totals rollup so
// the breakdown matches the server's full-set totals.
export function normalizeFlagCounts(
  raw: Record<string, number>,
): Map<import("../types/receipt").RedFlagKind, number> {
  const m = new Map<import("../types/receipt").RedFlagKind, number>()
  for (const [rule, n] of Object.entries(raw)) {
    const k = normalizeFlag(rule)
    if (k) m.set(k, (m.get(k) ?? 0) + n)
  }
  return m
}

function mapSession(r: RawSession): import("../types/receipt").Session {
  const start = Date.parse(r.started_at)
  const end = r.ended_at ? Date.parse(r.ended_at) : null
  const duration_ms = end && !isNaN(start) && !isNaN(end) ? end - start : 0
  const flags = normalizeFlags(r.flags)
  return {
    id: r.id,
    user_email: r.user,
    started_at: r.started_at,
    ended_at: r.ended_at,
    duration_ms,
    tool_count: r.tools_count ?? 0,
    cost_usd: r.cost_usd ?? 0,
    tokens_input: r.tokens_input ?? 0,
    tokens_output: r.tokens_output ?? 0,
    flag_count: flags.length,
    flags,
    title: r.title ?? null,
    workspace_id: r.workspace_id ?? null,
    is_public: Boolean(r.is_public),
    grade: r.grade ?? null,
  }
}

export async function listSessions(filters: Filters): Promise<SessionList> {
  if (USE_MOCKS) return mockListSessions(filters)
  const raw = await apiFetch<{
    items: RawSession[]
    total?: number
    totals?: import("../types/receipt").SessionTotals
  }>(`/sessions${qs(filters) ? `?${qs(filters)}` : ""}`)
  return {
    items: (raw.items ?? []).map(mapSession),
    total: raw.total ?? (raw.items ?? []).length,
    // Server-computed fleet totals over the FULL filtered set (not the page).
    totals: raw.totals,
  }
}

// ── Activity feed (GET /activity) ──────────────────────────────────────────
// Group-scoped curated event stream — what agents DID, newest first. Backend
// scopes it by the same visibility wall as the sessions list.
interface RawActivity {
  id: number
  session_id: string
  ts: string
  user: string
  agent: string
  kind: string
  tool?: string | null
  path?: string | null
  flags?: string[]
}

function mapActivity(r: RawActivity): import("../types/receipt").ActivityItem {
  return {
    id: r.id,
    session_id: r.session_id,
    at: r.ts,
    user_email: r.user,
    agent: r.agent,
    kind: r.kind,
    tool: r.tool ?? null,
    path: r.path ?? null,
    flags: normalizeFlags(r.flags),
  }
}

export async function listActivity(
  filters: Filters,
): Promise<import("../types/receipt").ActivityList> {
  const raw = await apiFetch<{ items: RawActivity[] }>(
    `/activity${qs(filters) ? `?${qs(filters)}` : ""}`,
  )
  return { items: (raw.items ?? []).map(mapActivity) }
}

// Lightweight count probe for the /welcome activation gate. Sends ?limit=1 so
// the backend doesn't serialize a full page just to decide zero-vs-nonzero.
export async function getSessionsCount(): Promise<{ total: number }> {
  if (USE_MOCKS) {
    const res = await mockListSessions({})
    return { total: res.total }
  }
  const raw = await apiFetch<{ items: RawSession[]; total?: number }>(
    `/sessions?limit=1`,
  )
  return { total: raw.total ?? (raw.items ?? []).length }
}

interface RawEvent {
  id: number
  ts: string
  kind: string
  tool?: string | null
  path?: string | null
  content?: string | null
  output?: string | null
  flags?: string[]
  duration_ms?: number | null
  group_key?: string | null
  tool_input?: Record<string, unknown> | null
  cost_usd?: number | null
  tokens_input?: number | null
  tokens_output?: number | null
  raw?: Record<string, unknown> | null
}

interface RawSessionDetail extends RawSession {
  events: RawEvent[]
  files_changed?: Array<{ path: string; op: string; additions?: number; deletions?: number }>
  summary?: string | null
  tools_called?: string[]
  score?: import("../types/receipt").SessionScore | null
}

function mapEventType(kind: string): import("../types/receipt").EventType {
  switch (kind) {
    case "tool_use":
    case "tool_call":
      return "tool_call"
    case "file_change":
      return "file_change"
    case "error":
      return "error"
    default:
      return "message"
  }
}

export async function getSession(id: string): Promise<SessionDetail> {
  if (USE_MOCKS) return mockGetSession(id)
  const raw = await apiFetch<RawSessionDetail>(`/sessions/${id}`)
  const base = mapSession(raw)
  return {
    ...base,
    // Split the raw event stream into timeline + usage channels.
    // Usage (kind=token) events power the rail TokenPanel + Hero sparkline;
    // keeping them out of the visible timeline avoids polluting the
    // conversation flow with per-call token readouts.
    usage_events: (raw.events ?? [])
      .filter((e) => e.kind === "token")
      .map((e) => {
        // Backend puts the full usage breakdown (input_tokens, cache_read,
        // cache_creation, output_tokens, etc) under `tool_input` via the
        // tailer's raw.tool_input path. `raw` itself isn't exposed by
        // EventOut, so we rely on tool_input being populated.
        const ti = (e.tool_input ?? {}) as Record<string, unknown>
        const model =
          typeof ti["model"] === "string"
            ? (ti["model"] as string)
            : (typeof e.tool === "string" ? e.tool : undefined)
        return {
          id: String(e.id),
          session_id: raw.id,
          at: e.ts,
          type: "message" as const,
          tool: "usage",
          tool_name: model ?? "usage",
          content: e.content ?? undefined,
          tokens_input: e.tokens_input ?? undefined,
          tokens_output: e.tokens_output ?? undefined,
          cost_usd: e.cost_usd ?? undefined,
          tool_input: ti,
        }
      }),
    events: (raw.events ?? [])
      // Filter out session_start/session_end (frame markers) and kind=token
      // (usage accounting — rolled up into the Hero cost sparkline + the
      // rail TokenPanel, never shown inline in the timeline flow).
      .filter(
        (e) =>
          e.kind !== "session_start" &&
          e.kind !== "session_end" &&
          e.kind !== "token",
      )
      .map((e) => {
        const type = mapEventType(e.kind)
        const tool = e.tool ?? undefined
        const path = e.path ?? undefined
        const content = e.content ?? undefined
        const base: import("../types/receipt").SessionEvent = {
          id: String(e.id),
          session_id: raw.id,
          at: e.ts,
          type,
          tool,
          tool_name: tool,
          path,
          file_path: path,
          content,
          output: e.output ?? undefined,
          duration_ms: e.duration_ms ?? undefined,
          group_key: e.group_key ?? undefined,
          tool_input: e.tool_input ?? undefined,
          cost_usd: e.cost_usd ?? undefined,
          tokens_input: e.tokens_input ?? undefined,
          tokens_output: e.tokens_output ?? undefined,
          flag: normalizeFlags(e.flags)[0],
          flags: normalizeFlags(e.flags),
        }
        // Route `content` into the type-specific field so timeline renderers
        // (which read error_message / text / content per kind) each see their payload.
        if (type === "error") base.error_message = content
        else if (type === "message") base.text = content
        if (type === "file_change" && tool) {
          // Backend doesn't persist file_op yet — infer from tool name.
          base.file_op = tool === "Write" ? "create" : "edit"
        }
        return base
      }),
    files_changed: (raw.files_changed ?? []).map((f) => ({
      path: f.path,
      op: (f.op ?? "edit") as import("../types/receipt").FileOp,
      additions: f.additions ?? 0,
      deletions: f.deletions ?? 0,
    })),
    summary: raw.summary ?? null,
    score: raw.score ?? null,
  }
}

/**
 * Download the full audit trail for a session as a JSON Blob. Bypasses the
 * apiFetch JSON-parse path because we want the browser to trigger a file
 * download via a synthetic <a download>. The backend route sets the
 * Content-Disposition header with `receipt-<id>.json`.
 */
export async function exportSessionTrail(id: string): Promise<void> {
  const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(id)}/trail`, {
    credentials: "include",
  })
  if (!res.ok) {
    const body = await res.text().catch(() => "")
    throw new ApiError(res.status, body || `Export failed (${res.status})`)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `receipt-${id}.json`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export async function postSummary(id: string): Promise<Summary> {
  if (USE_MOCKS) return mockGetSummary(id)
  return apiFetch<Summary>(`/sessions/${id}/summary`, { method: "POST" })
}

export async function getSummary(id: string): Promise<Summary> {
  if (USE_MOCKS) return mockGetSummary(id)
  return apiFetch<Summary>(`/sessions/${id}/summary`)
}

// ---- Issue #79 — share opt-in ----

export interface ShareResponse {
  session_id: string
  is_public: boolean
  public_url: string | null
}

export interface ShareConsent {
  consented: boolean
  at: string | null
}

/** Flip a session public. Idempotent server-side — re-POST returns the
 *  same canonical URL. 404 on cross-user, 401 on unauth. */
export async function shareSession(id: string): Promise<ShareResponse> {
  return apiFetch<ShareResponse>(`/sessions/${encodeURIComponent(id)}/share`, {
    method: "POST",
    body: JSON.stringify({ source: "dashboard" }),
  })
}

/** Flip a session back to private. Idempotent. */
export async function revokeShareSession(id: string): Promise<ShareResponse> {
  return apiFetch<ShareResponse>(
    `/sessions/${encodeURIComponent(id)}/share/revoke`,
    { method: "POST" },
  )
}

// ---- TSU-54 — shareable receipt PNG (local image export) ----
// The share artifact is the IMAGE the owner exports from their own instance —
// no hosted URL. Authed (cookie) + owner-only; 404 on cross-user.

/** Fetch the self-contained receipt PNG blob for a session. */
export async function fetchReceiptPng(id: string): Promise<Blob> {
  const res = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(id)}/receipt.png`,
    { credentials: "include" },
  )
  if (!res.ok) {
    const body = await res.text().catch(() => "")
    throw new ApiError(res.status, body || `Receipt render failed (${res.status})`)
  }
  return res.blob()
}

/** Trigger a browser download of the receipt PNG (synthetic <a download>). */
export async function downloadReceiptPng(id: string): Promise<void> {
  const blob = await fetchReceiptPng(id)
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `yoru-receipt-${id}.png`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** Copy the receipt PNG to the clipboard (async-clipboard ClipboardItem).
 *  Throws if the browser lacks image-clipboard support — caller falls back. */
export async function copyReceiptPng(id: string): Promise<void> {
  const blob = await fetchReceiptPng(id)
  if (typeof ClipboardItem === "undefined" || !navigator.clipboard?.write) {
    throw new Error("Image clipboard not supported in this browser")
  }
  await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })])
}

/** TSU-55 — download the session-replay GIF (the shareable replay artifact).
 *  Authed (cookie) + owner-only; 404 on cross-user. */
export async function downloadReplayGif(id: string): Promise<void> {
  const res = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(id)}/replay.gif`,
    { credentials: "include" },
  )
  if (!res.ok) {
    const body = await res.text().catch(() => "")
    throw new ApiError(res.status, body || `Replay render failed (${res.status})`)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `yoru-replay-${id}.gif`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

/** Read the caller's public-share consent state (set once, never cleared). */
export async function getShareConsent(): Promise<ShareConsent> {
  return apiFetch<ShareConsent>("/account/share-consent")
}

/** Mark the caller as having acknowledged the public-share disclosure.
 *  Idempotent — returns the original stamp on subsequent calls. */
export async function postShareConsent(): Promise<ShareConsent> {
  return apiFetch<ShareConsent>("/account/share-consent", { method: "POST" })
}

// Billing — mirror of C1 backend shapes (BILLING-PLANS-V1.md §3).
// (`Plan`, `OrgsMe`, `ORGS_ME_KEY` are declared near the top so the 402
// interceptor in apiFetch can reference them.)

export interface CheckoutRequest {
  plan: Plan
  seats?: number
  success_url: string
  cancel_url: string
}

export interface CheckoutResponse {
  checkout_url: string
  session_id: string
}

export async function postCheckoutSession(
  plan: Plan,
  seats: number = 1,
  cycle: "monthly" | "annual" = "monthly",
): Promise<CheckoutResponse> {
  const body = {
    plan,
    cycle,
    seats,
    success_url: window.location.origin + "/settings/billing?upgraded=1",
    cancel_url: window.location.href,
  }
  if (USE_MOCKS) {
    return {
      checkout_url: "/settings/billing?upgraded=1&mock=1",
      session_id: "cs_mock_frontend",
    }
  }
  return apiFetch<CheckoutResponse>("/billing/checkout-session", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export interface PortalSessionResponse {
  portal_url: string
}

export type PortalTargetPlan = "pro" | "team" | "cancel"
export type PortalTargetCycle = "monthly" | "annual"

export async function postPortalSession(
  returnUrl: string = `${window.location.origin}/settings/billing`,
  targetPlan?: PortalTargetPlan,
  targetCycle: PortalTargetCycle = "monthly",
): Promise<PortalSessionResponse> {
  const body: Record<string, unknown> = { return_url: returnUrl }
  if (targetPlan) {
    body.target_plan = targetPlan
    body.target_cycle = targetCycle
  }
  return apiFetch<PortalSessionResponse>("/billing/portal-session", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

// ───── CLI tokens (Phase E) ─────

export interface UserTokenItem {
  id: string
  label?: string | null
  token_type?: string | null
  machine_hostname?: string | null
  created_at: string
  last_used_at?: string | null
  revoked_at?: string | null
}

export interface ServiceTokenItem {
  id: string
  org_id: string
  label?: string | null
  machine_hostname?: string | null
  scopes?: string[] | null
  created_at: string
  last_used_at?: string | null
  revoked_at?: string | null
  minted_by_user_id?: string | null
}

export interface ServiceTokenCreated {
  token: string
  id: string
  org_id: string
  label: string
  created_at: string
}

export async function listMyTokens(): Promise<UserTokenItem[]> {
  return apiFetch<UserTokenItem[]>("/auth/hook-tokens")
}

export async function revokeMyToken(id: string): Promise<void> {
  await apiFetch<void>(`/auth/hook-token/${id}`, { method: "DELETE" })
}

export async function listServiceTokens(orgId: string): Promise<ServiceTokenItem[]> {
  return apiFetch<ServiceTokenItem[]>(
    `/auth/service-tokens?org_id=${encodeURIComponent(orgId)}`,
  )
}

export async function createServiceToken(
  orgId: string,
  label: string,
): Promise<ServiceTokenCreated> {
  return apiFetch<ServiceTokenCreated>("/auth/service-token", {
    method: "POST",
    body: JSON.stringify({ org_id: orgId, label }),
  })
}

export async function revokeServiceToken(id: string): Promise<void> {
  await apiFetch<void>(`/auth/service-token/${id}`, { method: "DELETE" })
}

// ───── Workspaces (Phase W2) ─────

export interface Workspace {
  id: string
  name: string
  slug: string
  org_id: string | null
  owner_user_id: string
  settings: Record<string, unknown>
  created_at: string
  updated_at: string
}

export async function listWorkspaces(): Promise<Workspace[]> {
  return apiFetch<Workspace[]>("/me/workspaces")
}

export interface WorkspaceRepo {
  id: string
  workspace_id: string
  host: string
  owner: string
  repo: string
  full_name: string
  created_at: string
}

export async function listWorkspaceRepos(workspaceId: string): Promise<WorkspaceRepo[]> {
  return apiFetch<WorkspaceRepo[]>(`/me/workspaces/${workspaceId}/repos`)
}

export async function addWorkspaceRepo(
  workspaceId: string,
  body: { host: string; owner: string; repo: string },
): Promise<WorkspaceRepo> {
  return apiFetch<WorkspaceRepo>(`/me/workspaces/${workspaceId}/repos`, {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export async function removeWorkspaceRepo(
  workspaceId: string,
  repoId: string,
): Promise<void> {
  await apiFetch<void>(`/me/workspaces/${workspaceId}/repos/${repoId}`, {
    method: "DELETE",
  })
}

export interface WorkspaceCreate {
  name: string
  slug?: string
  org_id?: string | null
}

export async function createWorkspace(body: WorkspaceCreate): Promise<Workspace> {
  return apiFetch<Workspace>("/me/workspaces", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export async function patchWorkspace(
  id: string,
  body: { name?: string; settings?: Record<string, unknown> },
): Promise<Workspace> {
  return apiFetch<Workspace>(`/me/workspaces/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })
}

export async function deleteWorkspace(id: string): Promise<void> {
  await apiFetch<void>(`/me/workspaces/${id}`, { method: "DELETE" })
}

export async function promoteWorkspace(
  id: string,
  body: { org_name: string; org_slug?: string },
): Promise<{ organization: { id: string; name: string }; workspace: Workspace }> {
  return apiFetch(`/me/workspaces/${id}/promote`, {
    method: "POST",
    body: JSON.stringify(body),
  })
}

// ───── GitHub integration (Phase W3) ─────

export interface GithubStatus {
  connected: boolean
  github_login?: string | null
  connected_at?: string | null
  expires_at?: string | null
}

export interface GithubRepo {
  id: number
  full_name: string
  owner: string
  repo: string
  host: string
  private: boolean
  updated_at?: string | null
  mapped_workspace_id?: string | null
}

export async function getGithubStatus(): Promise<GithubStatus> {
  return apiFetch<GithubStatus>("/me/github")
}

export async function connectGithub(body: {
  provider_token: string
  scopes?: string[]
}): Promise<GithubStatus> {
  return apiFetch<GithubStatus>("/me/github/connect", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export async function disconnectGithub(): Promise<void> {
  await apiFetch<void>("/me/github", { method: "DELETE" })
}

export async function listGithubRepos(
  page: number = 1,
  per_page: number = 100,
): Promise<GithubRepo[]> {
  return apiFetch<GithubRepo[]>(
    `/me/github/repos?page=${page}&per_page=${per_page}`,
  )
}

export async function autoRouteRepos(body: {
  workspace_id: string
  repos: string[]
}): Promise<{ added: number; skipped_already_mapped: number; errors: number }> {
  return apiFetch("/me/github/auto-route", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export function buildGithubAuthUrl(redirectTo: string, scopes: string[] = ["read:user", "repo"]): string {
  const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string | undefined
  if (!supabaseUrl) throw new Error("VITE_SUPABASE_URL not set")
  const u = new URL(`${supabaseUrl.replace(/\/$/, "")}/auth/v1/authorize`)
  u.searchParams.set("provider", "github")
  u.searchParams.set("redirect_to", redirectTo)
  u.searchParams.set("scopes", scopes.join(" "))
  return u.toString()
}

// ───── Routing rules (Phase D-lite) ─────

export interface RouteRule {
  id: string
  user_id?: string | null
  org_id?: string | null
  scope: "user" | "org"
  priority: number
  match_type: "git_remote" | "cwd"
  match_pattern: string
  target_org_id?: string | null
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface RouteRuleCreate {
  match_type: "git_remote" | "cwd"
  match_pattern: string
  target_org_id?: string | null
  priority?: number
  enabled?: boolean
}

export async function listRouteRules(): Promise<RouteRule[]> {
  return apiFetch<RouteRule[]>("/me/route-rules")
}

export async function createRouteRule(body: RouteRuleCreate): Promise<RouteRule> {
  return apiFetch<RouteRule>("/me/route-rules", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export async function deleteRouteRule(id: string): Promise<void> {
  await apiFetch<void>(`/me/route-rules/${id}`, { method: "DELETE" })
}

/**
 * Default auto-route patterns for a newly-created or newly-joined org.
 * git_remote + cwd rules let the hook route repos + bare directories.
 * Caller typically shows these in a confirm dialog before inserting.
 */
export function defaultAutoRoutePatterns(orgSlug: string): RouteRuleCreate[] {
  return [
    {
      match_type: "git_remote",
      match_pattern: `*${orgSlug}/*`,
      priority: 50,
    },
    {
      match_type: "cwd",
      match_pattern: `*/${orgSlug}/*`,
      priority: 60,
    },
  ]
}


export interface VerifyResult {
  session_id: string
  intact: boolean
  event_count: number
  verified: number
  unchained_legacy: number
  broken_at_event_id: number | null
  checked_at: string
}

export function verifySession(id: string): Promise<VerifyResult> {
  return apiFetch<VerifyResult>(`/sessions/${encodeURIComponent(id)}/verify`)
}
