import { useMemo, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { formatRelative, EmptyState } from "@receipt/ui"
import { ApiError, getOrgIdentities, listOrganizations, type OrgIdentityItem } from "../lib/api"
import { deviceUrgency } from "../lib/device-urgency"

type Exception = "expired" | "never-used" | "stale" | "multi-machine"

const EXCEPTION_LABEL: Record<Exception, string> = {
  expired: "expired",
  "never-used": "never used",
  stale: "stale",
  "multi-machine": "multi-machine",
}

const EXCEPTION_RANK: Record<Exception, number> = {
  expired: 0,
  "never-used": 1,
  stale: 2,
  "multi-machine": 3,
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function identityLabel(item: OrgIdentityItem): string {
  return item.identity_label?.trim() || "(no label)"
}

// A dev with 2+ distinct hostnames among their non-revoked identities is
// worth a look — could be a legit second machine, could be a token that
// leaked and got re-paired elsewhere. Not itself proof of anything wrong,
// which is why it ranks behind expired/never-used/stale above.
function multiMachineUsers(items: OrgIdentityItem[]): Set<string> {
  const byUser = new Map<string, Set<string>>()
  for (const it of items) {
    if (it.status === "revoked" || !it.machine_hostname) continue
    if (!byUser.has(it.user)) byUser.set(it.user, new Set())
    byUser.get(it.user)!.add(it.machine_hostname)
  }
  const flagged = new Set<string>()
  for (const [user, hosts] of byUser) if (hosts.size >= 2) flagged.add(user)
  return flagged
}

function identityException(item: OrgIdentityItem, multiMachine: Set<string>): Exception | null {
  if (item.status === "revoked") return null
  if (item.status === "expired") return "expired"
  const urgency = deviceUrgency(item.last_used_at)
  if (urgency === "never-used") return "never-used"
  if (urgency === "stale") return "stale"
  if (multiMachine.has(item.user)) return "multi-machine"
  return null
}

export function OrgIdentitiesPage() {
  const [selectedOrg, setSelectedOrg] = useState<string>("")
  const [showRevoked, setShowRevoked] = useState(false)

  const { data: orgs = [] } = useQuery({
    queryKey: ["orgs", "switcher"],
    queryFn: listOrganizations,
  })
  const activeOrg = selectedOrg || orgs[0]?.id || ""

  const query = useQuery({
    queryKey: ["org-identities", activeOrg],
    queryFn: () => getOrgIdentities(activeOrg),
    enabled: !!activeOrg,
    retry: false,
  })

  const notAuthorized = query.error instanceof ApiError && query.error.status === 403

  const multiMachine = useMemo(() => multiMachineUsers(query.data ?? []), [query.data])
  const items = query.data ?? []
  const active = items.filter((i) => i.status !== "revoked")
  const revoked = items.filter((i) => i.status === "revoked")
  const exceptions = active
    .map((item) => ({ item, exception: identityException(item, multiMachine) }))
    .filter((x): x is { item: OrgIdentityItem; exception: Exception } => x.exception !== null)
    .sort((a, b) => EXCEPTION_RANK[a.exception] - EXCEPTION_RANK[b.exception])

  const byOwner = useMemo(() => {
    const groups = new Map<string, OrgIdentityItem[]>()
    const visible = showRevoked ? items : active
    for (const it of visible) {
      if (!groups.has(it.user)) groups.set(it.user, [])
      groups.get(it.user)!.push(it)
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [items, active, showRevoked])

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <header className="flex flex-wrap items-baseline justify-between gap-3 border-b border-dashed border-rule pb-4">
        <div>
          <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
            Org admin
          </p>
          <h1 className="mt-2 font-sans text-3xl font-semibold text-ink">
            Connected identities
          </h1>
          <p className="mt-2 text-sm text-ink-muted">
            Every paired device across the org — anomalies first, then grouped by dev.
          </p>
        </div>
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
      </header>

      {orgs.length === 0 ? (
        <EmptyState
          heading="No organizations yet"
          body="Connected identities are scoped to an organization."
        />
      ) : query.isLoading ? (
        <p role="status" className="px-1 py-3 font-mono text-caption text-ink-muted">
          Loading…
        </p>
      ) : notAuthorized ? (
        // Server-side require_org_admin already made the call — never render
        // partial data or the raw 403 body, just the clean denied state.
        <EmptyState
          heading="Not authorized"
          body="Only an org owner/admin can view this org's connected identities."
        />
      ) : query.isError ? (
        <p role="alert" className="rounded-sm border border-rule border-l-2 border-l-flag-env bg-surface p-4 font-mono text-caption text-ink">
          {query.error instanceof Error ? query.error.message : "Failed to load identities."}
        </p>
      ) : items.length === 0 ? (
        <EmptyState heading="No identities yet" body="No devices have paired into this org." />
      ) : (
        <div className="space-y-8">
          <section className="space-y-3" aria-label="Identity exceptions">
            <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
              Exceptions
            </h2>
            {exceptions.length === 0 ? (
              <p className="rounded border border-dashed border-rule bg-surface px-4 py-3 text-sm text-ink-muted">
                No anomalies — every identity checked in recently, none expired.
              </p>
            ) : (
              <ul className="divide-y divide-dashed divide-rule overflow-hidden rounded border border-rule bg-surface">
                {exceptions.map(({ item, exception }) => (
                  <li key={item.id} className="flex flex-wrap items-baseline gap-2 px-4 py-2 text-sm">
                    <span className="rounded-sm bg-flag-migration/10 px-1.5 py-0.5 font-mono text-micro uppercase tracking-wider text-flag-migration">
                      {EXCEPTION_LABEL[exception]}
                    </span>
                    <span className="font-semibold text-ink">{item.user}</span>
                    <span className="text-ink-muted">{identityLabel(item)}</span>
                    {item.machine_hostname && (
                      <span className="font-mono text-micro text-ink-faint">{item.machine_hostname}</span>
                    )}
                    <span className="ml-auto font-mono text-micro text-ink-faint">
                      {item.last_used_at
                        ? `last seen ${formatRelative(item.last_used_at)}`
                        : `paired ${formatRelative(item.created_at)}, never used`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="space-y-3">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
                By dev · {byOwner.length}
              </h2>
              {revoked.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowRevoked((v) => !v)}
                  className="font-mono text-micro uppercase tracking-wider text-ink-faint hover:text-ink"
                >
                  {showRevoked ? "hide revoked" : `+ ${revoked.length} revoked`}
                </button>
              )}
            </div>
            <div className="space-y-4">
              {byOwner.map(([user, devices]) => (
                <div key={user} className="rounded border border-rule bg-surface">
                  <p className="border-b border-dashed border-rule px-4 py-2 font-mono text-caption font-semibold text-ink">
                    {user} <span className="text-ink-faint">· {devices.length}</span>
                  </p>
                  <ul className="divide-y divide-dashed divide-rule">
                    {devices.map((d) => (
                      <li key={d.id} className="flex flex-wrap items-baseline gap-2 px-4 py-2 text-sm">
                        <span className="text-ink">{identityLabel(d)}</span>
                        {d.machine_hostname && (
                          <span className="font-mono text-micro text-ink-muted">{d.machine_hostname}</span>
                        )}
                        <StatusBadge status={d.status} />
                        <span className="ml-auto font-mono text-micro text-ink-faint" title={formatDate(d.created_at)}>
                          {d.last_used_at ? `last seen ${formatRelative(d.last_used_at)}` : "never used"}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: OrgIdentityItem["status"] }) {
  if (status === "active") return null
  const style =
    status === "revoked"
      ? "bg-sunken text-ink-muted"
      : "bg-flag-migration/10 text-flag-migration"
  return (
    <span className={`rounded-sm px-1.5 py-0.5 font-mono text-micro uppercase tracking-wider ${style}`}>
      {status}
    </span>
  )
}

// Exported for tests only.
export { identityException, multiMachineUsers }
