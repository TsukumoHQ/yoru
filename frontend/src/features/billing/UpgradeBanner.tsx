import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import {
  ApiError,
  apiFetch,
  ORGS_ME_KEY,
  postCheckoutSession,
  type OrgsMe,
  type Plan,
} from "../../lib/api"
import { toast } from "../../components/Toaster"

// Single source of truth for ['orgs','me'] (US-V4-1 AC #6). Endpoint is
// pending backend US-18 — on 404/5xx we fall back to a non-exceeded state
// so the banner stays hidden in dev. A 402 from any other endpoint flips
// the shared cache via apiFetch's interceptor — we pick it up on next read.
// Yoru exposes `/me/subscription` which returns the active SubscriptionResponse
// with plan_name + features. We normalize to the OrgsMe shape the UI expects.
interface MeSubscriptionRaw {
  plan_name?: string
  status?: string
  features?: Record<string, unknown>
}

async function fetchOrgsMe(): Promise<OrgsMe> {
  try {
    const sub = await apiFetch<MeSubscriptionRaw | null>("/me/subscription")
    const plan = (sub?.plan_name ?? "Free").toLowerCase()
    // Map Yoru plan names → the legacy OrgsMe.plan union.
    const planKey: Plan =
      plan === "pro" || plan === "team" || plan === "org"
        ? (plan as Plan)
        : "free"
    return { plan: planKey, quota_exceeded: false }
  } catch (err) {
    if (err instanceof ApiError && err.status === 402) {
      return { plan: "free", quota_exceeded: true }
    }
    if (err instanceof ApiError && err.status === 404) {
      // No subscription yet — trigger backfill hasn't fired. Treat as Free.
      return { plan: "free", quota_exceeded: false }
    }
    return { plan: "free", quota_exceeded: false }
  }
}

export function UpgradeBanner() {
  const [starting, setStarting] = useState(false)
  const { data } = useQuery<OrgsMe>({
    queryKey: ORGS_ME_KEY,
    queryFn: fetchOrgsMe,
    retry: false,
  })

  if (!data?.quota_exceeded) return null

  async function onUpgrade() {
    if (starting) return
    setStarting(true)
    try {
      const res = await postCheckoutSession("team")
      window.location.href = res.checkout_url
    } catch (err) {
      setStarting(false)
      const detail = err instanceof Error ? err.message : String(err)
      toast.error("Couldn't start upgrade", detail)
    }
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="mb-6 flex flex-col gap-3 border-b border-dashed border-rule border-l-4 border-l-flag-migration bg-flag-migration/10 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-3">
        <span className="font-mono text-caption uppercase tracking-wider text-flag-migration">
          UPGRADE
        </span>
        <p className="text-sm text-ink">
          You&apos;ve hit your free tier session limit. Upgrade to Pro to keep ingesting.
        </p>
      </div>
      <button
        type="button"
        onClick={() => { void onUpgrade() }}
        disabled={starting}
        className="inline-flex items-center gap-1.5 self-start rounded-sm bg-accent-500 px-4 py-2 text-sm font-semibold text-primary-950 hover:bg-accent-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper disabled:opacity-60 sm:self-auto"
      >
        {starting && (
          <svg
            className="h-3 w-3 animate-spin"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
            <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
          </svg>
        )}
        {starting ? "Starting checkout…" : "Upgrade now"}
      </button>
    </div>
  )
}
