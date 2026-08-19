import { useSearchParams } from "react-router-dom"
import type { Filters, VcsProvider } from "./types"

/** Canonical, ordered VCS provider slugs — mirrors the backend registry
 *  (receipt/vcs.py KNOWN_PROVIDERS). The `?vcs=` filter only accepts these;
 *  anything else is ignored client-side (and would 400 server-side). */
export const KNOWN_VCS_PROVIDERS: readonly VcsProvider[] = [
  "github",
  "gitlab",
  "bitbucket",
  "azure",
]

function parseVcs(raw: string | null): VcsProvider | undefined {
  const v = raw?.trim().toLowerCase()
  return v && (KNOWN_VCS_PROVIDERS as readonly string[]).includes(v)
    ? (v as VcsProvider)
    : undefined
}

export function parseFilters(params: URLSearchParams): Filters {
  const f: Filters = {}
  const user = params.get("user")?.trim()
  if (user) f.user = user
  const from = params.get("from")?.trim()
  if (from) f.date_from = from
  const to = params.get("to")?.trim()
  if (to) f.date_to = to
  if (params.get("flag") === "1") f.flag_only = true
  const min = params.get("min_cost")
  if (min !== null && min !== "") {
    const n = Number(min)
    if (Number.isFinite(n) && n > 0) f.min_cost = n
  }
  const ws = params.get("workspace_id")?.trim()
  if (ws) f.workspace_id = ws
  const vcs = parseVcs(params.get("vcs"))
  if (vcs) f.vcs = vcs
  return f
}

export function useFilters(): Filters {
  const [params] = useSearchParams()
  return parseFilters(params)
}
