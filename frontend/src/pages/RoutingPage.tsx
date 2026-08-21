import { useMemo } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  apiFetch,
  deleteRouteRule,
  listRouteRules,
  type RouteRule,
} from "../lib/api"
import { toast } from "../components/Toaster"

interface Organization {
  id: string
  name: string
  slug: string
  type: "personal" | "team"
}

interface OrgListResponse {
  items: Organization[]
}

const ROUTE_RULES_KEY = ["me", "route-rules"] as const
const ORGS_KEY = ["me", "organizations"] as const

function formatMatchType(t: RouteRule["match_type"]): string {
  return t === "git_remote" ? "git remote" : "working dir"
}

export function RoutingPage() {
  const qc = useQueryClient()

  const { data: rules = [], isLoading } = useQuery({
    queryKey: ROUTE_RULES_KEY,
    queryFn: listRouteRules,
  })

  const { data: orgsResp } = useQuery({
    queryKey: ORGS_KEY,
    queryFn: () => apiFetch<OrgListResponse>("/me/organizations"),
  })
  const orgNameById = useMemo(() => {
    const m = new Map<string, string>()
    for (const o of Array.isArray(orgsResp?.items) ? orgsResp.items : []) m.set(o.id, o.name)
    return m
  }, [orgsResp])

  const deleteMut = useMutation({
    mutationFn: deleteRouteRule,
    onSuccess: () => {
      toast.success("Rule deleted")
      qc.invalidateQueries({ queryKey: ROUTE_RULES_KEY })
    },
    onError: (e) => toast.error(e.message),
  })

  const userRules = rules.filter((r) => r.scope === "user")
  const orgRules = rules.filter((r) => r.scope === "org")

  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-12">
      <header className="mb-10">
        <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">
          Settings
        </p>
        <h1 className="mt-2 font-serif text-h2 text-ink">Routing</h1>
        <p className="mt-2 max-w-2xl text-sm text-ink-muted">
          When the Yoru CLI streams events, the server picks which
          workspace receives them based on these rules. Patterns are matched
          against the project's <code className="font-mono text-caption">git remote</code>{" "}
          URL or its working directory.
        </p>
        <p className="mt-2 max-w-2xl text-caption text-ink-muted">
          New rules are created automatically when you join or create an
          organization. A full editor is coming — for now, delete + recreate
          via the auto-prompt at org creation.
        </p>
      </header>

      {isLoading ? (
        <p className="text-sm text-ink-muted">Loading rules…</p>
      ) : rules.length === 0 ? (
        <div className="rounded border border-dashed border-rule px-5 py-8 text-center">
          <p className="font-medium text-ink">No routing rules yet</p>
          <p className="mt-2 text-caption text-ink-muted">
            All your Claude Code events land in your personal workspace.
            Create or join an organization to auto-add a routing rule.
          </p>
          <a
            href="/settings/organizations"
            className="mt-4 inline-block rounded border border-ink bg-ink px-3 py-1.5 font-mono text-caption uppercase tracking-wider text-canvas hover:bg-ink-muted"
          >
            Go to organizations
          </a>
        </div>
      ) : (
        <>
          {userRules.length > 0 && (
            <section className="mb-10">
              <h2 className="mb-4 font-serif text-h3 text-ink">My rules</h2>
              <RuleTable
                rules={userRules}
                orgNameById={orgNameById}
                onDelete={(id) => deleteMut.mutate(id)}
                deletingId={deleteMut.isPending ? deleteMut.variables : null}
                canDelete
              />
            </section>
          )}

          {orgRules.length > 0 && (
            <section>
              <h2 className="mb-4 font-serif text-h3 text-ink">
                Inherited from organizations
              </h2>
              <p className="mb-3 text-caption text-ink-muted">
                Admins of your organizations define these. Read-only here.
              </p>
              <RuleTable
                rules={orgRules}
                orgNameById={orgNameById}
                onDelete={() => {}}
                deletingId={null}
                canDelete={false}
              />
            </section>
          )}
        </>
      )}
    </div>
  )
}

function RuleTable({
  rules,
  orgNameById,
  onDelete,
  deletingId,
  canDelete,
}: {
  rules: RouteRule[]
  orgNameById: Map<string, string>
  onDelete: (id: string) => void
  deletingId: string | null
  canDelete: boolean
}) {
  return (
    <div className="overflow-x-auto rounded border border-rule">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-rule bg-sunken text-left font-mono text-micro uppercase tracking-wider text-ink-muted">
            <th className="px-3 py-2">Match</th>
            <th className="px-3 py-2">Pattern</th>
            <th className="px-3 py-2">Target</th>
            <th className="px-3 py-2">Priority</th>
            {canDelete && <th className="w-24 px-3 py-2"></th>}
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.id} className="border-b border-rule last:border-0">
              <td className="px-3 py-2 font-mono text-caption text-ink-muted">
                {formatMatchType(r.match_type)}
              </td>
              <td className="px-3 py-2 font-mono text-ink">{r.match_pattern}</td>
              <td className="px-3 py-2 text-ink">
                {r.target_org_id
                  ? orgNameById.get(r.target_org_id) ?? r.target_org_id.slice(0, 8)
                  : "Personal"}
              </td>
              <td className="px-3 py-2 font-mono text-caption text-ink-muted">
                {r.priority}
              </td>
              {canDelete && (
                <td className="px-3 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => onDelete(r.id)}
                    disabled={deletingId === r.id}
                    className="rounded border border-rule px-2 py-0.5 font-mono text-caption text-ink hover:bg-sunken disabled:opacity-40"
                  >
                    {deletingId === r.id ? "…" : "Delete"}
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
