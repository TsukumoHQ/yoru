import { useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { formatCost, EmptyState, KNOWN_VCS_PROVIDERS } from "@receipt/ui"
import {
  apiFetch,
  getTokenAnalytics,
  getTokenAnalyticsSessions,
  type TokenAnalyticsBucket,
  type TokenAnalyticsOrgScope,
  type TokenAnalyticsProject,
  type TokenAnalyticsScope,
  type TokenAnalyticsSessionItem,
  type TokenAnalyticsSpender,
} from "../lib/api"

type Bucket = "day" | "week"
type View = "org" | "own"
type DevSort = "cost" | "tokens" | "user"

// Same team-org shape TokensPage reads off /me/organizations — a caller only
// ever sees orgs they belong to (member) or every org (super-admin, per the
// M3 contract), so "pick one to see org-wide spend" never leaks a colleague's
// org by guessing an id.
interface Organization {
  id: string
  name: string
  slug: string
  type: "personal" | "team"
}

interface OrgListResponse {
  items: Organization[]
}

const ORGS_KEY = ["me", "organizations"] as const

// A spike is "the newest bucket costs meaningfully more than the trailing
// average of the buckets before it" — not a statistical model, just enough
// to pull a real jump above the fold instead of making the reader eyeball a
// row of numbers. Needs >=2 buckets and a non-zero trailing average (an
// account with zero prior spend that starts spending isn't a "spike").
export interface Spike {
  bucket: TokenAnalyticsBucket
  ratio: number
  trailingAvgCost: number
}

const SPIKE_RATIO = 1.5

export function detectSpike(series: TokenAnalyticsBucket[]): Spike | null {
  if (series.length < 2) return null
  const latest = series[series.length - 1]
  const prior = series.slice(0, -1)
  const trailingAvgCost = prior.reduce((s, b) => s + b.cost_usd, 0) / prior.length
  if (trailingAvgCost <= 0) return null
  const ratio = latest.cost_usd / trailingAvgCost
  if (ratio < SPIKE_RATIO) return null
  return { bucket: latest, ratio, trailingAvgCost }
}

// Period delta for the hero: the backend returns one fixed window (no
// separate "previous period" query), so the delta compares the trailing
// half of the visible series against the leading half — a coarse but honest
// "trending up/down within this range", not a calendar-aligned prior period.
export interface PeriodDelta {
  pct: number
  direction: "up" | "down" | "flat"
}

export function periodDelta(series: TokenAnalyticsBucket[]): PeriodDelta | null {
  if (series.length < 2) return null
  const mid = Math.floor(series.length / 2)
  const early = series.slice(0, mid)
  const late = series.slice(mid)
  const earlySum = early.reduce((s, b) => s + b.cost_usd, 0)
  const lateSum = late.reduce((s, b) => s + b.cost_usd, 0)
  if (earlySum <= 0) return lateSum > 0 ? { pct: 100, direction: "up" } : null
  const pct = ((lateSum - earlySum) / earlySum) * 100
  if (Math.abs(pct) < 1) return { pct: 0, direction: "flat" }
  return { pct, direction: pct > 0 ? "up" : "down" }
}

function formatBucketDate(iso: string, bucket: Bucket): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  return bucket === "week" ? `Week of ${day}` : day
}

function vcsLabel(vcs: string | null): string {
  if (!vcs) return "No repo"
  return vcs.charAt(0).toUpperCase() + vcs.slice(1)
}

function isEmptyScope(own: TokenAnalyticsScope, org: TokenAnalyticsOrgScope | null): boolean {
  const ownEmpty = own.totals.tokens_input === 0 && own.totals.tokens_output === 0
  const orgEmpty = !org || (org.totals.tokens_input === 0 && org.totals.tokens_output === 0)
  return ownEmpty && orgEmpty
}

export function UsagePage() {
  const qc = useQueryClient()
  const [bucket, setBucket] = useState<Bucket>("day")
  const [selectedOrg, setSelectedOrg] = useState<string>("")
  const [viewOverride, setViewOverride] = useState<View | null>(null)
  const [selectedDev, setSelectedDev] = useState<string | null>(null)
  const [selectedProject, setSelectedProject] = useState<string | null>(null)

  const { data: orgsResp } = useQuery({
    queryKey: ORGS_KEY,
    queryFn: () => apiFetch<OrgListResponse>("/me/organizations"),
  })
  const orgs = useMemo(
    () => (Array.isArray(orgsResp?.items) ? orgsResp.items : []).filter((o) => o.type === "team"),
    [orgsResp],
  )
  const activeOrg = selectedOrg || orgs[0]?.id || ""
  // CTO org-first: default to the org view once the caller has >=1 team org,
  // unless they explicitly picked "You".
  const view: View = viewOverride ?? (orgs.length > 0 ? "org" : "own")

  function resetDrill() {
    setSelectedDev(null)
    setSelectedProject(null)
  }

  // Own scope is the core view and always fetched bare (no org_id) — it must
  // never fail just because the org-wide add-on below isn't authorized for
  // this caller.
  const ownQuery = useQuery({
    queryKey: ["analytics", "tokens", "own", bucket],
    queryFn: () => getTokenAnalytics({ bucket }).then((r) => r.own),
  })

  // Org scope is a best-effort overlay. /me/organizations lists every org the
  // caller belongs to regardless of role, but /analytics/tokens?org_id=
  // 403s for anyone who isn't that org's owner/admin (require_org_admin) — a
  // plain member picking their own team is expected, not an error. Fail
  // silent here (retry: false, error read as "no org data") so it never
  // blanks the own-scope numbers above; only the org fetch is best-effort.
  const orgQuery = useQuery({
    queryKey: ["analytics", "tokens", "org", bucket, activeOrg],
    queryFn: () => getTokenAnalytics({ bucket, orgId: activeOrg }).then((r) => r.org),
    enabled: !!activeOrg,
    retry: false,
  })
  const org = orgQuery.data ?? null

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-dashed border-rule pb-4">
        <div>
          <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
            Settings
          </p>
          <h1 className="mt-2 font-sans text-3xl font-semibold text-ink">
            Token usage
          </h1>
          <p className="mt-2 text-sm text-ink-muted">
            Spend anomalies first — spikes and top spenders, then the org
            cost console.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {orgs.length > 0 && (
            <label className="flex items-center gap-2 font-mono text-caption text-ink-muted">
              <span>Org</span>
              <select
                value={activeOrg}
                onChange={(e) => {
                  setSelectedOrg(e.target.value)
                  resetDrill()
                }}
                className="rounded-sm border border-rule bg-paper px-2 py-1 text-ink focus:border-ink focus:outline-none"
              >
                {orgs.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex rounded-sm border border-rule" role="group" aria-label="Time bucket">
            {(["day", "week"] as const).map((b) => (
              <button
                key={b}
                type="button"
                onClick={() => setBucket(b)}
                aria-pressed={bucket === b}
                className={
                  "px-3 py-1 font-mono text-caption uppercase tracking-wider first:rounded-l-sm last:rounded-r-sm " +
                  (bucket === b ? "bg-ink text-paper" : "text-ink-muted hover:bg-sunken")
                }
              >
                {b}
              </button>
            ))}
          </div>
        </div>
      </header>

      {orgs.length > 0 && (
        <div className="flex rounded-sm border border-rule w-fit" role="tablist" aria-label="Usage scope">
          {(["org", "own"] as const).map((v) => (
            <button
              key={v}
              type="button"
              role="tab"
              aria-selected={view === v}
              onClick={() => {
                setViewOverride(v)
                resetDrill()
              }}
              className={
                "px-4 py-1.5 font-mono text-caption uppercase tracking-wider first:rounded-l-sm last:rounded-r-sm " +
                (view === v ? "bg-ink text-paper" : "text-ink-muted hover:bg-sunken")
              }
            >
              {v === "org" ? "Org" : "You"}
            </button>
          ))}
        </div>
      )}

      {ownQuery.isLoading ? (
        <p role="status" className="px-1 py-3 font-mono text-caption text-ink-muted">
          Loading usage…
        </p>
      ) : ownQuery.isError ? (
        <ErrorBanner
          message={ownQuery.error instanceof Error ? ownQuery.error.message : "Failed to load usage."}
          onRetry={() => qc.invalidateQueries({ queryKey: ["analytics", "tokens", "own"] })}
        />
      ) : ownQuery.data && isEmptyScope(ownQuery.data, org) ? (
        <EmptyState
          heading="No usage yet"
          body="Token consumption shows up here once sessions start recording — run `yoru init` to pair a machine."
        />
      ) : ownQuery.data ? (
        <UsageBody
          own={ownQuery.data}
          org={org}
          orgPending={orgQuery.isLoading}
          bucket={bucket}
          view={view}
          activeOrg={activeOrg}
          selectedDev={selectedDev}
          selectedProject={selectedProject}
          onSelectDev={setSelectedDev}
          onSelectProject={setSelectedProject}
          onClearDrill={resetDrill}
        />
      ) : null}
    </div>
  )
}

function UsageBody({
  own,
  org,
  orgPending,
  bucket,
  view,
  activeOrg,
  selectedDev,
  selectedProject,
  onSelectDev,
  onSelectProject,
  onClearDrill,
}: {
  own: TokenAnalyticsScope
  org: TokenAnalyticsOrgScope | null
  orgPending: boolean
  bucket: Bucket
  view: View
  activeOrg: string
  selectedDev: string | null
  selectedProject: string | null
  onSelectDev: (dev: string | null) => void
  onSelectProject: (project: string | null) => void
  onClearDrill: () => void
}) {
  const ownSpike = detectSpike(own.series)
  const orgSpike = detectSpike(org?.series ?? [])
  const topSpenders = org?.top_spenders ?? []
  const hasException = !!ownSpike || !!orgSpike || topSpenders.length > 0

  const scope: TokenAnalyticsScope = view === "org" && org ? org : own
  const scopeLabel = view === "org" && org ? "Org" : "Your"

  return (
    <div className="space-y-8">
      <section className="space-y-3" aria-label="Spend exceptions">
        <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Exceptions
        </h2>
        {!hasException ? (
          <p className="rounded border border-dashed border-rule bg-surface px-4 py-3 text-sm text-ink-muted">
            No spend anomalies in this range — usage tracked normal.
          </p>
        ) : (
          <div className="space-y-3">
            {ownSpike && (
              <SpikeCallout label="Your usage" spike={ownSpike} bucket={bucket} />
            )}
            {orgSpike && (
              <SpikeCallout label="Org usage" spike={orgSpike} bucket={bucket} />
            )}
            {topSpenders.length > 0 && <TopSpenders spenders={topSpenders} />}
          </div>
        )}
      </section>

      {view === "org" && !org && (
        orgPending ? (
          <p role="status" className="px-1 py-3 font-mono text-caption text-ink-muted">
            Loading org spend…
          </p>
        ) : (
          <p className="rounded border border-dashed border-rule bg-surface px-4 py-3 text-sm text-ink-muted">
            No org-wide data — you need org admin to see org spend. Showing
            your own usage below.
          </p>
        )
      )}

      <section className="space-y-3">
        <HeroMetric label={scopeLabel} totals={scope.totals} series={scope.series} />
        <TrendChart series={scope.series} bucket={bucket} />
      </section>

      {view === "org" && org && (
        <section className="grid gap-6 md:grid-cols-2">
          <DevRosterTable
            devs={org.by_dev}
            selectedDev={selectedDev}
            onSelectDev={onSelectDev}
          />
          <ProjectBreakdown
            projects={org.by_project}
            selectedProject={selectedProject}
            onSelectProject={onSelectProject}
          />
        </section>
      )}

      {view === "org" && org && (selectedDev || selectedProject !== null) && (
        <SessionDrillList
          orgId={activeOrg}
          dev={selectedDev}
          project={selectedProject}
          onClear={onClearDrill}
        />
      )}
    </div>
  )
}

function HeroMetric({
  label,
  totals,
  series,
}: {
  label: string
  totals: TokenAnalyticsScope["totals"]
  series: TokenAnalyticsBucket[]
}) {
  const delta = periodDelta(series)
  return (
    <div className="rounded border border-rule bg-surface px-5 py-4">
      <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
        {label} spend, this period
      </p>
      <div className="mt-1 flex flex-wrap items-baseline gap-3">
        <span className="font-mono text-4xl font-semibold tabular-nums text-ink">
          {formatCost(totals.cost_usd)}
        </span>
        <span className="text-sm text-ink-muted">
          {(totals.tokens_input + totals.tokens_output).toLocaleString()} tokens
        </span>
        {delta && delta.direction !== "flat" && (
          <span
            className={
              "font-mono text-caption tabular-nums " +
              (delta.direction === "up" ? "text-flag-migration" : "text-op-create")
            }
          >
            {delta.direction === "up" ? "▲" : "▼"} {Math.abs(delta.pct).toFixed(0)}%{" "}
            <span className="text-ink-faint">vs earlier in range</span>
          </span>
        )}
      </div>
    </div>
  )
}

// Bar trend over day/week buckets. Single series → one hue (accent-500), no
// legend needed. Thin bars, rounded data-ends anchored to the baseline, a 2px
// gap between bars (dataviz mark spec). A native <table> lives behind
// <details> as the accessible/table-view fallback.
function TrendChart({ series, bucket }: { series: TokenAnalyticsBucket[]; bucket: Bucket }) {
  const [hover, setHover] = useState<number | null>(null)
  const width = 640
  const height = 160
  const padTop = 8
  const padBottom = 20
  const plotH = height - padTop - padBottom

  if (series.length === 0) {
    return (
      <p className="rounded border border-dashed border-rule bg-surface px-4 py-3 text-sm text-ink-muted">
        No activity in this range.
      </p>
    )
  }

  const max = Math.max(...series.map((b) => b.cost_usd), 0.0001)
  const gap = 2
  const barW = Math.max(2, width / series.length - gap)
  const tickEvery = Math.max(1, Math.ceil(series.length / 8))

  return (
    <div className="rounded border border-rule bg-surface p-4">
      <div className="relative">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full overflow-visible"
          role="img"
          aria-label={`Cost trend, ${series.length} ${bucket} buckets, peak ${formatCost(max)}`}
        >
          {series.map((b, i) => {
            const h = Math.max(1, (b.cost_usd / max) * plotH)
            const x = i * (barW + gap)
            const y = padTop + (plotH - h)
            const isHover = hover === i
            return (
              <g key={b.bucket_start}>
                <rect
                  x={x}
                  y={y}
                  width={barW}
                  height={h}
                  rx={Math.min(4, barW / 2)}
                  fill={`rgb(var(--accent-500) / ${isHover ? 1 : 0.75})`}
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover((h2) => (h2 === i ? null : h2))}
                />
                {i % tickEvery === 0 && (
                  <text
                    x={x + barW / 2}
                    y={height - 4}
                    textAnchor="middle"
                    className="fill-ink-faint font-mono"
                    fontSize={9}
                  >
                    {formatBucketDate(b.bucket_start, bucket)}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
        {hover !== null && series[hover] && (
          <div
            role="tooltip"
            className="pointer-events-none absolute -top-1 -translate-y-full rounded border border-rule bg-paper px-2 py-1 font-mono text-micro text-ink shadow-sm"
            style={{ left: `${((hover + 0.5) / series.length) * 100}%`, transform: "translate(-50%, -100%)" }}
          >
            {formatBucketDate(series[hover].bucket_start, bucket)} · {formatCost(series[hover].cost_usd)}
          </div>
        )}
      </div>
      <details className="mt-2">
        <summary className="cursor-pointer font-mono text-micro uppercase tracking-wider text-ink-muted">
          View as table
        </summary>
        <div className="mt-2">
          <SeriesTable series={series} bucket={bucket} />
        </div>
      </details>
    </div>
  )
}

function DevRosterTable({
  devs,
  selectedDev,
  onSelectDev,
}: {
  devs: TokenAnalyticsSpender[]
  selectedDev: string | null
  onSelectDev: (dev: string | null) => void
}) {
  const [sortKey, setSortKey] = useState<DevSort>("cost")
  const [sortDesc, setSortDesc] = useState(true)

  const sorted = useMemo(() => {
    const rows = [...devs]
    rows.sort((a, b) => {
      let cmp = 0
      if (sortKey === "user") cmp = a.user.localeCompare(b.user)
      else if (sortKey === "tokens") cmp = a.tokens_input + a.tokens_output - (b.tokens_input + b.tokens_output)
      else cmp = a.cost_usd - b.cost_usd
      return sortDesc ? -cmp : cmp
    })
    return rows
  }, [devs, sortKey, sortDesc])

  const maxCost = Math.max(...devs.map((d) => d.cost_usd), 0.0001)

  function toggleSort(key: DevSort) {
    if (key === sortKey) setSortDesc((d) => !d)
    else {
      setSortKey(key)
      setSortDesc(true)
    }
  }

  if (devs.length === 0) {
    return (
      <div className="rounded border border-rule bg-surface p-4">
        <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">Devs</p>
        <p className="mt-2 text-sm text-ink-muted">No dev activity in this range.</p>
      </div>
    )
  }

  return (
    <div className="rounded border border-rule bg-surface p-4">
      <div className="flex items-center justify-between">
        <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">
          Devs ({devs.length})
        </p>
        <div className="flex gap-2 font-mono text-micro text-ink-faint">
          {(["user", "tokens", "cost"] as const).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => toggleSort(k)}
              className={"uppercase hover:text-ink " + (sortKey === k ? "text-ink underline" : "")}
            >
              {k}
              {sortKey === k ? (sortDesc ? " ↓" : " ↑") : ""}
            </button>
          ))}
        </div>
      </div>
      <ul className="mt-2 divide-y divide-dashed divide-rule">
        {sorted.map((d) => (
          <li key={d.user}>
            <button
              type="button"
              onClick={() => onSelectDev(selectedDev === d.user ? null : d.user)}
              aria-pressed={selectedDev === d.user}
              className={
                "flex w-full items-center gap-3 py-1.5 text-left text-sm hover:bg-sunken " +
                (selectedDev === d.user ? "bg-sunken" : "")
              }
            >
              <span className="w-32 shrink-0 truncate text-ink">{d.user}</span>
              <span
                role="img"
                aria-label={`${formatCost(d.cost_usd)} of ${formatCost(maxCost)} peak`}
                className="h-2 flex-1 overflow-hidden rounded-sm bg-sunken"
              >
                <span
                  className="block h-full rounded-sm bg-accent-500"
                  style={{ width: `${Math.max(2, (d.cost_usd / maxCost) * 100)}%` }}
                />
              </span>
              <span className="w-16 shrink-0 text-right font-mono text-caption text-ink-muted">
                {formatCost(d.cost_usd)}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ProjectBreakdown({
  projects,
  selectedProject,
  onSelectProject,
}: {
  projects: TokenAnalyticsProject[]
  selectedProject: string | null
  onSelectProject: (project: string | null) => void
}) {
  // Fixed order (KNOWN_VCS_PROVIDERS), never re-cycled by filter state —
  // color/identity follows the provider, not its rank in this period.
  const order = [...KNOWN_VCS_PROVIDERS, null]
  const byVcs = new Map(projects.map((p) => [p.vcs, p]))
  const rows = order.map((vcs) => byVcs.get(vcs)).filter((p): p is TokenAnalyticsProject => !!p)
  const maxCost = Math.max(...projects.map((p) => p.cost_usd), 0.0001)

  if (rows.length === 0) {
    return (
      <div className="rounded border border-rule bg-surface p-4">
        <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">Projects</p>
        <p className="mt-2 text-sm text-ink-muted">No project activity in this range.</p>
      </div>
    )
  }

  return (
    <div className="rounded border border-rule bg-surface p-4">
      <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">
        Projects ({rows.length})
      </p>
      <ul className="mt-2 divide-y divide-dashed divide-rule">
        {rows.map((p) => {
          const key = p.vcs ?? "__none__"
          const active = selectedProject === (p.vcs ?? "")
          const barAndCost = (
            <>
              <span
                role="img"
                aria-label={`${formatCost(p.cost_usd)} of ${formatCost(maxCost)} peak`}
                className="h-2 flex-1 overflow-hidden rounded-sm bg-sunken"
              >
                <span
                  className="block h-full rounded-sm bg-accent-500"
                  style={{ width: `${Math.max(2, (p.cost_usd / maxCost) * 100)}%` }}
                />
              </span>
              <span className="w-16 shrink-0 text-right font-mono text-caption text-ink-muted">
                {formatCost(p.cost_usd)}
              </span>
            </>
          )
          if (p.vcs === null) {
            // Unfilterable server-side: project="" means no-filter, not "no repo" — see fix history.
            return (
              <li key={key} className="flex w-full items-center gap-3 py-1.5 text-sm">
                <span className="w-24 shrink-0 truncate text-ink-muted">{vcsLabel(p.vcs)}</span>
                {barAndCost}
              </li>
            )
          }
          return (
            <li key={key}>
              <button
                type="button"
                onClick={() => onSelectProject(active ? null : p.vcs)}
                aria-pressed={active}
                className={
                  "flex w-full items-center gap-3 py-1.5 text-left text-sm hover:bg-sunken " +
                  (active ? "bg-sunken" : "")
                }
              >
                <span className="w-24 shrink-0 truncate text-ink">{vcsLabel(p.vcs)}</span>
                {barAndCost}
              </button>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function SessionDrillList({
  orgId,
  dev,
  project,
  onClear,
}: {
  orgId: string
  dev: string | null
  project: string | null
  onClear: () => void
}) {
  const sessionsQuery = useQuery({
    queryKey: ["analytics", "tokens", "sessions", orgId, dev, project],
    queryFn: () =>
      getTokenAnalyticsSessions({
        orgId,
        dev: dev ?? undefined,
        project: project || undefined,
        limit: 50,
      }),
  })

  return (
    <section className="space-y-3" aria-label="Session drill">
      <div className="flex items-center justify-between">
        <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Sessions
          {dev ? ` · ${dev}` : ""}
          {project !== null ? ` · ${vcsLabel(project || null)}` : ""}
        </h2>
        <button
          type="button"
          onClick={onClear}
          className="font-mono text-micro uppercase tracking-wider text-ink-muted hover:text-ink"
        >
          Clear filter
        </button>
      </div>
      {sessionsQuery.isLoading ? (
        <p role="status" className="px-1 py-2 font-mono text-caption text-ink-muted">
          Loading sessions…
        </p>
      ) : sessionsQuery.isError ? (
        <p role="alert" className="rounded border border-rule bg-surface px-4 py-3 text-sm text-ink-muted">
          Failed to load sessions.
        </p>
      ) : sessionsQuery.data && sessionsQuery.data.items.length > 0 ? (
        <>
          <ul className="divide-y divide-dashed divide-rule overflow-hidden rounded border border-rule bg-surface">
            {sessionsQuery.data.items.map((s: TokenAnalyticsSessionItem) => (
              <li key={s.id}>
                <Link
                  to={`/s/${s.id}`}
                  className="flex items-center justify-between gap-4 px-4 py-2 text-sm hover:bg-sunken"
                >
                  <span className="truncate text-ink">{s.user}</span>
                  <span className="font-mono text-caption text-ink-muted">{vcsLabel(s.vcs)}</span>
                  <span className="text-ink-muted">
                    {(s.tokens_input + s.tokens_output).toLocaleString()} tokens
                  </span>
                  <span className="font-mono text-caption text-ink">{formatCost(s.cost_usd)}</span>
                </Link>
              </li>
            ))}
          </ul>
          <p className="font-mono text-micro text-ink-faint">
            Showing {sessionsQuery.data.items.length} of {sessionsQuery.data.total}
          </p>
        </>
      ) : (
        <p className="rounded border border-dashed border-rule bg-surface px-4 py-3 text-sm text-ink-muted">
          No sessions match this filter.
        </p>
      )}
    </section>
  )
}

function SpikeCallout({
  label,
  spike,
  bucket,
}: {
  label: string
  spike: Spike
  bucket: Bucket
}) {
  return (
    <div
      role="alert"
      className="rounded border border-flag-migration/40 bg-flag-migration/5 p-4"
    >
      <p className="font-mono text-micro uppercase tracking-wider text-flag-migration">
        Spike · {label}
      </p>
      <p className="mt-2 text-sm text-ink">
        {formatBucketDate(spike.bucket.bucket_start, bucket)} cost{" "}
        <strong>{formatCost(spike.bucket.cost_usd)}</strong> — {spike.ratio.toFixed(1)}×
        the trailing average ({formatCost(spike.trailingAvgCost)}).
      </p>
    </div>
  )
}

function TopSpenders({ spenders }: { spenders: TokenAnalyticsSpender[] }) {
  return (
    <div className="rounded border border-rule bg-surface p-4">
      <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">
        Top spenders
      </p>
      <ul className="mt-2 divide-y divide-dashed divide-rule">
        {spenders.map((s) => (
          <li key={s.user} className="flex items-center justify-between gap-4 py-1.5 text-sm">
            <span className="truncate text-ink">{s.user}</span>
            <span className="font-mono text-caption text-ink-muted">
              {formatCost(s.cost_usd)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function SeriesTable({ series, bucket }: { series: TokenAnalyticsBucket[]; bucket: Bucket }) {
  if (series.length === 0) {
    return (
      <p className="rounded border border-dashed border-rule bg-surface px-4 py-3 text-sm text-ink-muted">
        No activity in this range.
      </p>
    )
  }
  return (
    <ul className="divide-y divide-dashed divide-rule overflow-hidden rounded border border-rule bg-surface">
      {series
        .slice()
        .reverse()
        .map((b) => (
          <li
            key={b.bucket_start}
            className="flex items-center justify-between gap-4 px-4 py-2 text-sm"
          >
            <span className="font-mono text-caption text-ink-muted">
              {formatBucketDate(b.bucket_start, bucket)}
            </span>
            <span className="text-ink-muted">
              {(b.tokens_input + b.tokens_output).toLocaleString()} tokens
            </span>
            <span className="font-mono text-caption text-ink">{formatCost(b.cost_usd)}</span>
          </li>
        ))}
    </ul>
  )
}

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-sm border border-rule border-l-2 border-l-flag-env bg-surface p-4">
      <p className="font-mono text-caption text-ink">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 rounded-sm border border-rule px-3 py-1 font-mono text-micro text-ink hover:bg-sunken"
      >
        Retry
      </button>
    </div>
  )
}
