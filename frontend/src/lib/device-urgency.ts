// Shared exception-first triage for paired-device rows (DEC-yoru-product-
// principle-1): a device that's never checked in, or gone quiet, is what
// needs a look — not a chronological wall. Used by both the dev-view
// (TokensPage "My machines") and the CTO org-wide view (OrgIdentitiesPage).
export const STALE_DAYS = 30
const DAY_MS = 86_400_000

export type DeviceUrgency = "never-used" | "stale" | "active"

export function deviceUrgency(lastUsedAt: string | null | undefined): DeviceUrgency {
  if (!lastUsedAt) return "never-used"
  const idleDays = (Date.now() - new Date(lastUsedAt).getTime()) / DAY_MS
  return idleDays >= STALE_DAYS ? "stale" : "active"
}

export function idleDays(lastUsedAt: string): number {
  return Math.floor((Date.now() - new Date(lastUsedAt).getTime()) / DAY_MS)
}

export const URGENCY_RANK: Record<DeviceUrgency, number> = {
  "never-used": 0,
  stale: 1,
  active: 2,
}
