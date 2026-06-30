export type RedFlagKind =
  | "secret-pattern"
  | "shell-destructive"
  | "db-destructive"
  | "migration-edit"
  | "env-mutation"
  | "ci-config-edit"

export type EventType = "tool_call" | "file_change" | "error" | "message"

export type FileOp = "create" | "edit" | "delete"

export interface Session {
  id: string
  user_email: string
  started_at: string
  ended_at: string | null
  duration_ms: number
  tool_count: number
  cost_usd: number
  tokens_input: number
  tokens_output: number
  flag_count: number
  flags: RedFlagKind[]
  /** Auto-derived from the first user prompt; falls back to id when null. */
  title?: string | null
  /** Target workspace resolved at ingestion. NULL means routing fell back
   *  to the user's personal workspace (or unknown if resolve_workspace
   *  couldn't reach Supabase). */
  workspace_id?: string | null
  /** Opt-in public share flag (#79). True when POST /sessions/{id}/share
   *  has flipped this session visible at /s/{id}. Defaults false. */
  is_public?: boolean
  /** A–F verdict (TSU-249), so the feed card can lead with the grade without
   *  fetching each session's detail. Same compute_score the detail uses. */
  grade?: string | null
}

export interface SessionEvent {
  id: string
  session_id: string
  at: string
  type: EventType
  tool_name?: string
  tool_input?: unknown
  file_path?: string
  file_op?: FileOp
  error_message?: string
  text?: string
  /** Primary flag (backward compat) = flags[0]. */
  flag?: RedFlagKind
  /** All red flags triggered by this event (an event can trip multiple). */
  flags?: RedFlagKind[]
  /** Tool stdout/stderr/error preview (capped 800 chars). */
  output?: string
  // SESSION-DETAIL-V1 additions (backend EventOut v1, all optional / non-breaking).
  tool?: string
  path?: string
  content?: string
  duration_ms?: number
  group_key?: string
  /** Per-event cost in USD (backend EventOut) — powers Hero cost sparkline. */
  cost_usd?: number
  tokens_input?: number
  tokens_output?: number
}

export interface FileChanged {
  path: string
  op: FileOp
  additions: number
  deletions: number
}

export interface SessionScore {
  overall: number
  throughput: number
  reliability: number
  safety: number
  grade: string
  breakdown: Record<string, unknown>
}

export interface SessionDetail extends Session {
  events: SessionEvent[]
  /** Per-call token+cost breakdown (kind=token on the wire); not shown inline. */
  usage_events?: SessionEvent[]
  files_changed: FileChanged[]
  summary?: string | null
  score?: SessionScore | null
}

export interface Summary {
  text: string
  model: string
  cached_at: string
}

export interface Filters {
  user?: string
  date_from?: string
  date_to?: string
  flag_only?: boolean
  min_cost?: number
  workspace_id?: string
  /** Pagination — the feed pages through with offset (backend list_sessions
   *  already supports limit/offset, started_at DESC). */
  limit?: number
  offset?: number
}

export interface SessionList {
  items: Session[]
  total: number
}

/** One curated row of the group-scoped activity feed (GET /activity): what an
 *  agent DID — a tool call, file edit, or error — with the owning session's
 *  user+agent so the row reads "who · which agent — action". */
export interface ActivityItem {
  id: number
  session_id: string
  at: string
  user_email: string
  agent: string
  /** Raw backend kind: "tool_use" | "file_change" | "error". */
  kind: string
  tool?: string | null
  path?: string | null
  flags: RedFlagKind[]
}

export interface ActivityList {
  items: ActivityItem[]
}
