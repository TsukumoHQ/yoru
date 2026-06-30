import { useEffect, useState } from "react"
import { useSearchParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { listWorkspaces, type Workspace } from "../../lib/api"

function updateParam(
  params: URLSearchParams,
  key: string,
  value: string | null,
): URLSearchParams {
  const next = new URLSearchParams(params)
  if (value === null || value === "") next.delete(key)
  else next.set(key, value)
  return next
}

// Quick date ranges. Each sets `from` to N days ago (day granularity, matching
// the From/To date inputs) and clears `to`; "All" clears both. The backend
// from_ts filter scopes the list AND the fleet totals, so picking a range
// re-totals the whole set for that window.
function daysAgoISODate(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10) // YYYY-MM-DD
}
const RANGES: { label: string; days: number }[] = [
  { label: "24h", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
]

const chipCls = (active: boolean) =>
  "h-8 rounded-sm border px-2.5 font-mono text-micro tabular-nums " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
  (active
    ? "border-accent-500 bg-accent-500/10 text-accent-500"
    : "border-rule text-ink-muted hover:bg-sunken")

const labelCls =
  "flex flex-col gap-1 font-mono text-micro uppercase tracking-wider text-ink-faint"
const inputCls =
  "h-8 w-full rounded-sm border border-rule bg-surface px-2 " +
  "font-mono text-caption text-ink placeholder:text-ink-faint " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
  "disabled:opacity-50"

export function FilterBar() {
  const [params, setParams] = useSearchParams()

  const userParam = params.get("user") ?? ""
  const fromParam = params.get("from") ?? ""
  const toParam = params.get("to") ?? ""
  const flagOnly = params.get("flag") === "1"
  const minCostParam = params.get("min_cost") ?? ""
  const workspaceParam = params.get("workspace_id") ?? ""

  const workspacesQuery = useQuery<Workspace[]>({
    queryKey: ["me", "workspaces"],
    queryFn: listWorkspaces,
    staleTime: 60_000,
    meta: { silent: true },
  })
  const workspaces = workspacesQuery.data ?? []

  const [userDraft, setUserDraft] = useState(userParam)
  useEffect(() => setUserDraft(userParam), [userParam])

  useEffect(() => {
    if (userDraft === userParam) return
    const t = setTimeout(() => {
      setParams(
        (prev) => updateParam(prev, "user", userDraft.trim() || null),
        { replace: true },
      )
    }, 300)
    return () => clearTimeout(t)
  }, [userDraft, userParam, setParams])

  const setOne = (key: string, value: string | null) => {
    setParams((prev) => updateParam(prev, key, value))
  }

  const clearAll = () => setParams(new URLSearchParams())

  // Set `from` to a quick range (days ago) and clear `to`; days=null → all time.
  const setRange = (days: number | null) => {
    setParams((prev) => {
      const cleared = updateParam(prev, "to", null)
      return updateParam(cleared, "from", days === null ? null : daysAgoISODate(days))
    })
  }
  const activeRange = !toParam
    ? RANGES.find((r) => fromParam === daysAgoISODate(r.days))?.label ?? null
    : null
  const isAllTime = !fromParam && !toParam

  const anyActive =
    !!userParam || !!fromParam || !!toParam || flagOnly || !!minCostParam || !!workspaceParam

  return (
    <section
      aria-label="Filter sessions"
      className="flex flex-col gap-3 border-b border-rule pb-4 sm:flex-row sm:flex-wrap sm:items-end sm:gap-4"
    >
      <label className={labelCls + " w-full sm:w-52"}>
        <span>User</span>
        <input
          type="search"
          inputMode="email"
          value={userDraft}
          onChange={(e) => setUserDraft(e.target.value)}
          placeholder="alice@acme.dev"
          className={inputCls}
        />
      </label>

      <div className={labelCls}>
        <span>Range</span>
        <div className="flex gap-1">
          {RANGES.map((r) => (
            <button
              key={r.label}
              type="button"
              onClick={() => setRange(r.days)}
              aria-pressed={activeRange === r.label}
              className={chipCls(activeRange === r.label)}
            >
              {r.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => setRange(null)}
            aria-pressed={isAllTime}
            className={chipCls(isAllTime)}
          >
            All
          </button>
        </div>
      </div>

      <label className={labelCls + " w-full sm:w-40"}>
        <span>From</span>
        <input
          type="date"
          value={fromParam}
          onChange={(e) => setOne("from", e.target.value || null)}
          className={inputCls}
        />
      </label>

      <label className={labelCls + " w-full sm:w-40"}>
        <span>To</span>
        <input
          type="date"
          value={toParam}
          onChange={(e) => setOne("to", e.target.value || null)}
          className={inputCls}
        />
      </label>

      <label className={labelCls + " w-full sm:w-44"}>
        <span>Workspace</span>
        <select
          value={workspaceParam}
          onChange={(e) => setOne("workspace_id", e.target.value || null)}
          className={inputCls}
        >
          <option value="">All workspaces</option>
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
              {w.org_id ? " · org" : " · personal"}
            </option>
          ))}
        </select>
      </label>

      {/* Min-cost filter hidden pre-launch (see ROADMAP.md) — backend
          still accepts min_cost query param, UI surface just gone. */}

      <label className="flex items-center gap-2 font-mono text-caption text-ink-muted">
        <input
          type="checkbox"
          checked={flagOnly}
          onChange={(e) => setOne("flag", e.target.checked ? "1" : null)}
          className="h-4 w-4 cursor-pointer rounded-sm accent-accent-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
        />
        <span>Flagged only</span>
      </label>

      <button
        type="button"
        onClick={clearAll}
        disabled={!anyActive}
        className={
          "h-8 w-full rounded-sm border border-rule px-3 sm:ml-auto sm:w-auto " +
          "font-mono text-micro uppercase tracking-wider text-ink-muted " +
          "hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-40 " +
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
          "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
        }
      >
        Clear
      </button>
    </section>
  )
}
