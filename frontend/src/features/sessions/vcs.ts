import { KNOWN_VCS_PROVIDERS } from "@receipt/ui"
import type { VcsProvider } from "../../types/receipt"

/** Display metadata for each VCS provider slug. The slug order is fixed by the
 *  backend registry (receipt/vcs.py KNOWN_PROVIDERS) and re-exported as
 *  KNOWN_VCS_PROVIDERS — we key off it so a new provider only needs a label
 *  here, never a re-order. `short` is the compact per-card badge label. */
export const VCS_META: Record<VcsProvider, { label: string; short: string }> = {
  github: { label: "GitHub", short: "GitHub" },
  gitlab: { label: "GitLab", short: "GitLab" },
  bitbucket: { label: "Bitbucket", short: "Bitbucket" },
  azure: { label: "Azure", short: "Azure" },
}

/** Ordered provider slugs for the filter chip row + any per-provider iteration. */
export const VCS_PROVIDERS: readonly VcsProvider[] = KNOWN_VCS_PROVIDERS

export function vcsLabel(slug: VcsProvider): string {
  return VCS_META[slug]?.label ?? slug
}
