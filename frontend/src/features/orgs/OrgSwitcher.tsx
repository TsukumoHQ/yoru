// Studio super-admin cross-org switcher (yoru multi-tenant, doc trovex:44a3774a,
// slice 1). Lets a deployer super-admin scope the whole dashboard to one client
// org, or view all. Selection is stored in lib/org-scope and forwarded as
// `X-Organization-Id` on audit reads (lib/api.ts `orgHeaders`).
//
// Fully inert unless BOTH hold: the caller is a super-admin AND the instance is
// multi-tenant (`config.single_org === false`). On a single-org self-hosted box
// this renders nothing and changes no behaviour.
import { useEffect } from "react"
import { useQuery } from "@tanstack/react-query"
import { useSession } from "../../auth/useSession"
import { useInstanceConfig, useInstanceConfigResolved } from "../../lib/config"
import { listOrganizations } from "../../lib/api"
import { getSelectedOrgId, setSelectedOrgId, useSelectedOrgId } from "../../lib/org-scope"

export function OrgSwitcher() {
  const { user, loading: authLoading } = useSession()
  const config = useInstanceConfig()
  const configResolved = useInstanceConfigResolved()
  const selectedOrgId = useSelectedOrgId()

  const eligible = !!user?.is_super_admin && !config.single_org

  const { data: orgs = [] } = useQuery({
    queryKey: ["orgs", "switcher"],
    queryFn: listOrganizations,
    enabled: eligible,
    staleTime: 60_000,
    meta: { silent: true },
  })

  // Belt-and-suspenders alongside the signOut clear: if a selection survives
  // into a genuinely ineligible state (super-admin demoted mid-session, config
  // flips to single-org, or a session restored without a signOut), drop it so no
  // stale `X-Organization-Id` reaches the audit reads.
  //
  // Gate on BOTH signals being resolved: on initial load `user` is null until
  // /auth/session/me returns AND useInstanceConfig serves the single_org=true
  // placeholder until /config returns, so `eligible` is transiently false. Only
  // clear once we truly know the caller is not a super-admin — otherwise every
  // reload would wipe a legit super-admin's persisted selection.
  const resolved = !authLoading && configResolved
  useEffect(() => {
    if (resolved && !eligible && getSelectedOrgId() !== null) {
      setSelectedOrgId(null)
    }
  }, [resolved, eligible])

  if (!eligible) return null

  return (
    <label className="hidden items-center gap-1.5 md:inline-flex">
      <span className="sr-only">Organization scope</span>
      <span aria-hidden="true" className="font-mono text-micro uppercase tracking-wider text-ink-faint">
        org
      </span>
      <select
        value={selectedOrgId ?? ""}
        onChange={(e) => setSelectedOrgId(e.target.value || null)}
        title="Scope the dashboard to one client organization"
        className="rounded-sm border border-rule bg-surface px-2 py-1 font-mono text-caption text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
      >
        <option value="">All organizations</option>
        {orgs.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </label>
  )
}
