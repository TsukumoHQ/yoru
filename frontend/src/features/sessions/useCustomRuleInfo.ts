import { useQuery } from "@tanstack/react-query"
import { fetchCustomRuleInfo } from "../../lib/api"
import type { CustomRuleInfo } from "../../types/receipt"

/** Name/severity lookup for a session's `custom:<uuid>` red-flag hits, keyed
 *  by the org that owns the SESSION (not the studio super-admin's currently
 *  selected org — a cross-org view must resolve names against the session's
 *  own org). Cached 60s per org — the same set of rules backs every session
 *  in that org, so repeat session views on one org reuse a single fetch.
 *  Never throws: fetchCustomRuleInfo swallows fetch/404 errors to `{}`, so a
 *  custom badge just falls back to its short-id label rather than the page
 *  erroring out over rule-name metadata. */
export function useCustomRuleInfo(orgId: string | null | undefined) {
  const query = useQuery<Record<string, CustomRuleInfo>>({
    queryKey: ["custom-rule-info", orgId],
    queryFn: () => fetchCustomRuleInfo(orgId as string),
    enabled: Boolean(orgId),
    staleTime: 60_000,
  })
  return query.data ?? EMPTY
}

const EMPTY: Record<string, CustomRuleInfo> = {}
