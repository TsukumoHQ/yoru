import { useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { formatCost, EmptyState } from "@receipt/ui"
import {
  apiFetch,
  getTokenAnalytics,
  type TokenAnalyticsBucket,
  type TokenAnalyticsOrgScope,
  type TokenAnalyticsScope,
  type TokenAnalyticsSpender,
} from "../lib/api"

type Bucket = "day" | "week"

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

function formatBucketDate(iso: string, bucket: Bucket): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const day = d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
  return bucket === "week" ? `Week of ${day}` : day
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

  const { data: orgsResp } = useQuery({
    queryKey: ORGS_KEY,
    queryFn: () => apiFetch<OrgListResponse>("/me/organizations"),
  })
  const orgs = useMemo(
    () => (orgsResp?.items ?? []).filter((o) => o.type === "team"),
    [orgsResp],
  )
  const activeOrg = selectedOrg || orgs[0]?.id || ""

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
            Spend anomalies first — spikes and top spenders, then the raw
            breakdown.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {orgs.length > 1 && (
            <label className="flex items-center gap-2 font-mono text-caption text-ink-muted">
              <span>Org</span>
              <select
                value={activeOrg}
                onChange={(e) => setSelectedOrg(e.target.value)}
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
        <UsageBody own={ownQuery.data} org={org} bucket={bucket} />
      ) : null}
    </div>
  )
}

function UsageBody({
  own,
  org,
  bucket,
}: {
  own: TokenAnalyticsScope
  org: TokenAnalyticsOrgScope | null
  bucket: Bucket
}) {
  const ownSpike = detectSpike(own.series)
  const orgSpike = detectSpike(org?.series ?? [])
  const topSpenders = org?.top_spenders ?? []
  const hasException = !!ownSpike || !!orgSpike || topSpenders.length > 0

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

      <section className="space-y-3">
        <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Breakdown — {formatCost(own.totals.cost_usd)} total ·{" "}
          {(own.totals.tokens_input + own.totals.tokens_output).toLocaleString()} tokens
        </h2>
        <SeriesTable series={own.series} bucket={bucket} />
      </section>
    </div>
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
