import { useMemo } from "react"
import { formatCost } from "@receipt/ui"
import { normalizeFlagCounts } from "../../lib/api"
import type { Session, SessionList, RedFlagKind } from "../../types/receipt"

// Fleet-level rollup — the "totals" panel above the Receipts table. Uses the
// server-computed totals over the FULL filtered + visibility-scoped set (so it
// reflects every session, not just the loaded page). Falls back to summing the
// page only for legacy responses / mocks that omit `totals`. Cost is
// API-EQUIVALENT (per-token API rates), not a subscriber's bill.

const RUBRIC = "font-mono text-caption uppercase tracking-wider text-ink-faint"

const FLAG_LABEL: Record<RedFlagKind, string> = {
  "secret-pattern": "secret",
  "env-mutation": "env",
  "shell-destructive": "shell",
  "db-destructive": "db",
  "migration-edit": "migration",
  "ci-config-edit": "ci",
}
const FLAG_ORDER = Object.keys(FLAG_LABEL) as RedFlagKind[]

function fmtTok(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  if (n < 1_000_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  return `${(n / 1_000_000_000).toFixed(2)}B`
}

interface Totals {
  sessions: number
  toolCalls: number
  tokensIn: number
  tokensOut: number
  cost: number
  flagCount: number
  flagsByKind: Map<RedFlagKind, number>
  publicCount: number
}

function rollup(list: SessionList): Totals {
  // Preferred path: the server's true totals over the full filtered set.
  if (list.totals) {
    const t = list.totals
    return {
      sessions: list.total,
      toolCalls: t.tool_count,
      tokensIn: t.tokens_input,
      tokensOut: t.tokens_output,
      cost: t.cost_usd,
      flagCount: t.flag_count,
      flagsByKind: normalizeFlagCounts(t.flags_by_kind),
      publicCount: t.public_sessions,
    }
  }

  // Fallback (legacy responses / mocks without `totals`): sum the loaded page.
  const items = list.items
  const flagsByKind = new Map<RedFlagKind, number>()
  let toolCalls = 0
  let tokensIn = 0
  let tokensOut = 0
  let cost = 0
  let flagCount = 0
  let publicCount = 0
  for (const s of items as Session[]) {
    toolCalls += s.tool_count
    tokensIn += s.tokens_input ?? 0
    tokensOut += s.tokens_output ?? 0
    cost += s.cost_usd ?? 0
    flagCount += s.flag_count
    if (s.is_public) publicCount += 1
    for (const k of s.flags) flagsByKind.set(k, (flagsByKind.get(k) ?? 0) + 1)
  }
  return {
    sessions: list.total,
    toolCalls,
    tokensIn,
    tokensOut,
    cost,
    flagCount,
    flagsByKind,
    publicCount,
  }
}

export function FleetStats({ list }: { list: SessionList }) {
  const t = useMemo(() => rollup(list), [list])
  if (t.sessions === 0) return null

  return (
    <section
      aria-label="Fleet totals"
      className="rounded-sm border border-rule bg-surface"
    >
      <header className="flex items-baseline justify-between border-b border-dashed border-rule px-4 py-2">
        <h2 className={RUBRIC}>Fleet totals</h2>
        {t.publicCount > 0 && (
          <span className={`${RUBRIC} tabular-nums`}>{t.publicCount} shared</span>
        )}
      </header>

      <dl className="grid grid-cols-2 gap-px bg-rule sm:grid-cols-3 lg:grid-cols-5">
        <Tile label="sessions" value={String(t.sessions)} />
        <Tile label="tool calls" value={fmtTok(t.toolCalls)} />
        <Tile
          label="tokens"
          value={fmtTok(t.tokensIn + t.tokensOut)}
          sub={`${fmtTok(t.tokensIn)} in · ${fmtTok(t.tokensOut)} out`}
        />
        <Tile
          label="cost"
          value={formatCost(t.cost)}
          sub="api-equivalent"
          accent
        />
        <Tile
          label="red flags"
          value={String(t.flagCount)}
          sub={
            t.flagCount > 0
              ? FLAG_ORDER.filter((k) => t.flagsByKind.get(k))
                  .map((k) => `${FLAG_LABEL[k]} ${t.flagsByKind.get(k)}`)
                  .join(" · ")
              : "none"
          }
        />
      </dl>
    </section>
  )
}

function Tile({
  label,
  value,
  sub,
  accent,
}: {
  label: string
  value: string
  sub?: string
  accent?: boolean
}) {
  return (
    <div className="bg-surface px-4 py-3">
      <p className={RUBRIC}>{label}</p>
      <p
        className={
          "mt-0.5 font-mono text-2xl font-semibold tabular-nums " +
          (accent ? "text-accent-500" : "text-ink")
        }
      >
        {value}
      </p>
      {sub && (
        <p className="mt-0.5 font-mono text-micro tabular-nums text-ink-faint">{sub}</p>
      )}
    </div>
  )
}
