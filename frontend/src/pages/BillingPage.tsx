import { useMemo, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useSearchParams } from "react-router-dom"
import {
  apiFetch,
  ApiError,
  postCheckoutSession,
  postPortalSession,
  type Plan,
} from "../lib/api"
import { toast } from "../components/Toaster"

// ─── Types ──────────────────────────────────────────────────────────────────

interface PlanRow {
  id: string
  name: string
  description?: string
  price: number
  billing_period: "monthly" | "annual" | "one_time"
  is_active: boolean
}

interface Subscription {
  id?: string
  status?: string
  plan_id?: string
  plan_name?: string
  features?: Record<string, unknown>
  stripe_customer_id?: string | null
}

// Map a Yoru plan `name` ("Free" / "Pro" / "Team" / "Org") onto the
// POST /billing/checkout-session `plan` enum (lowercase, Polar SDK).
const CHECKOUT_KEY: Record<string, Plan> = {
  Free: "free",
  Pro:  "pro",
  Team: "team",
  Org:  "org",
}

const PLAN_ORDER = ["Free", "Pro", "Team", "Org"] as const
const PLAN_TIER: Record<string, number> = { Free: 0, Pro: 1, Team: 2, Org: 3 }

function tierCompare(a: string, b: string): number {
  return (PLAN_TIER[a] ?? 0) - (PLAN_TIER[b] ?? 0)
}

// Curated subset of feature keys surfaced on plan cards + their display
// order. The labels themselves now come from the features catalog
// (GET /features) so we can't drift from the backend source of truth.
// Ordering here is deliberate: billing axis (seats, events, retention)
// first, compliance flags (audit, SSO, SCIM, chain-hash) second,
// integration quotas (webhooks, API keys) last.
const FEATURE_ORDER = [
  "seats_max",
  "events_per_month",
  "retention_days",
  "audit_log",
  "sso_saml",
  "scim",
  "chain_hash_export",
  "outbound_webhooks_max",
  "api_keys_max",
] as const

interface FeatureCatalogRow {
  id: string
  key: string
  name: string
  description?: string | null
  type: "flag" | "quota"
  default_value: unknown
}

async function fetchFeatureCatalog(): Promise<FeatureCatalogRow[]> {
  try {
    const raw = await apiFetch<FeatureCatalogRow[] | { items: FeatureCatalogRow[] }>(
      "/features",
    )
    return Array.isArray(raw) ? raw : (raw?.items ?? [])
  } catch {
    return []
  }
}

// ─── Data ───────────────────────────────────────────────────────────────────

async function fetchPlans(): Promise<PlanRow[]> {
  try {
    const raw = await apiFetch<PlanRow[] | { items: PlanRow[] }>("/plans")
    const arr = Array.isArray(raw) ? raw : (raw?.items ?? [])
    return arr.filter((p) => p.is_active && p.billing_period === "monthly")
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return []
    throw err
  }
}

async function fetchMySubscription(): Promise<Subscription | null> {
  try {
    const r = await apiFetch<Subscription | null>("/me/subscription")
    return r
  } catch (err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 404)) {
      return null
    }
    throw err
  }
}

interface PlanFeatureRow {
  id: string
  key: string
  name: string
  type: "flag" | "quota"
  value: unknown
}

async function fetchPlanFeatures(planId: string): Promise<Record<string, unknown>> {
  try {
    const r = await apiFetch<{ id: string; features?: PlanFeatureRow[] }>(
      `/plans/${planId}`,
    )
    const out: Record<string, unknown> = {}
    for (const f of r?.features ?? []) {
      out[f.key] = f.value
    }
    return out
  } catch {
    return {}
  }
}

// ─── Formatters ─────────────────────────────────────────────────────────────

function formatFeatureValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—"
  if (typeof value === "boolean") return value ? "Yes" : "—"
  if (typeof value === "number") {
    if (value < 0) return "Unlimited"
    if (key === "events_per_month") {
      if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(0)}M`
      if (value >= 1_000) return `${(value / 1_000).toFixed(0)}k`
      return String(value)
    }
    return String(value)
  }
  if (typeof value === "object" && value !== null) {
    const obj = value as Record<string, unknown>
    // Quota features carry {"limit": N}
    if ("limit" in obj) {
      return formatFeatureValue(key, obj.limit)
    }
    // Flag features carry {"enabled": bool}
    if ("enabled" in obj) {
      return obj.enabled ? "Yes" : "—"
    }
    // Tiered-value features (future-proof) may carry {"value": X}
    if ("value" in obj) {
      return formatFeatureValue(key, obj.value)
    }
  }
  if (typeof value === "string") return value
  return "—"
}

const ANNUAL_DISCOUNT = 0.79 // -21% vs monthly × 12

function monthlyUnit(p: PlanRow): number {
  const n = typeof p.price === "number" ? p.price : Number(p.price)
  return Number.isFinite(n) ? n : 0
}

function formatPrice(p: PlanRow, cycle: "monthly" | "annual"): string {
  const monthly = monthlyUnit(p)
  if (monthly === 0) return "$0"
  if (cycle === "monthly") return `$${monthly.toFixed(0)}`
  // Annual: show effective monthly price
  const effective = monthly * 12 * ANNUAL_DISCOUNT / 12
  return `$${effective.toFixed(0)}`
}

// Org has a split fee: $99 base + $15 / seat / month. The `plans.price`
// column stores the base ($99); the per-seat add-on is a second Stripe price
// we attach at checkout time. Centralize the formula here so UI + rationale
// stay in one place.
const ORG_SEAT_ADDON_USD = 15

function computeMonthlyTotal(planName: string, basePrice: number, seats: number): number {
  if (planName === "Org") {
    return basePrice + ORG_SEAT_ADDON_USD * Math.max(0, seats)
  }
  if (planName === "Team" || planName === "Pro") {
    return basePrice * Math.max(1, seats)
  }
  return basePrice  // Free
}

function computeBillingTotal(
  planName: string,
  basePrice: number,
  seats: number,
  cycle: "monthly" | "annual",
): number {
  const monthly = computeMonthlyTotal(planName, basePrice, seats)
  if (cycle === "monthly") return monthly
  return monthly * 12 * ANNUAL_DISCOUNT
}

function extractSeatsCap(value: unknown): number {
  if (typeof value === "number") return value
  if (value && typeof value === "object" && "limit" in value) {
    const lim = (value as { limit: unknown }).limit
    if (typeof lim === "number") return lim
  }
  return 0
}

function priceCadence(p: PlanRow): string {
  if (p.name === "Free") return "forever"
  if (p.name === "Pro") return "/ mo"
  if (p.name === "Team") return "/ seat / mo"
  if (p.name === "Org") return "starts at / mo · custom quote"
  return "/ mo"
}

// ─── Page ───────────────────────────────────────────────────────────────────

export function BillingPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const upgradedFlag = searchParams.get("upgraded") === "1"
  const mockFlag = searchParams.get("mock") === "1"
  const mockPlan = searchParams.get("plan")
  const [banner, setBanner] = useState(upgradedFlag && !mockFlag)
  const [mockBanner, setMockBanner] = useState(mockFlag)
  const [upgradingPlan, setUpgradingPlan] = useState<string | null>(null)
  const [cycle, setCycle] = useState<"monthly" | "annual">("monthly")
  const queryClient = useQueryClient()

  const { data: plans = [], isLoading: plansLoading } = useQuery<PlanRow[]>({
    queryKey: ["plans"],
    queryFn: fetchPlans,
    staleTime: 5 * 60_000,
  })

  const { data: catalog = [] } = useQuery<FeatureCatalogRow[]>({
    queryKey: ["features", "catalog"],
    queryFn: fetchFeatureCatalog,
    staleTime: 5 * 60_000,
    meta: { silent: true },
  })

  const { data: subscription, isLoading: subLoading } = useQuery<Subscription | null>({
    queryKey: ["me", "subscription"],
    queryFn: fetchMySubscription,
    staleTime: 60_000,
  })

  // Keep only the features we want to surface on the cards, in the order
  // defined by FEATURE_ORDER. Falls back to the catalog order if a key
  // listed in FEATURE_ORDER isn't in the catalog (dev/stale catalog).
  const featureRows = useMemo(() => {
    const byKey = new Map(catalog.map((f) => [f.key, f]))
    const out: { key: string; label: string }[] = []
    const seen = new Set<string>()
    for (const k of FEATURE_ORDER) {
      const f = byKey.get(k)
      if (f) {
        out.push({ key: f.key, label: f.name })
        seen.add(k)
      }
    }
    // Append any catalog features not explicitly ordered (new backend
    // features show up automatically, no frontend change needed).
    for (const f of catalog) {
      if (!seen.has(f.key)) {
        out.push({ key: f.key, label: f.name })
      }
    }
    return out
  }, [catalog])

  const currentPlanName = subscription?.plan_name ?? "Free"

  // Dismiss the upgraded banner once the subscription reflects the new plan.
  if (banner && subscription && subscription.plan_name !== "Free") {
    setBanner(false)
    const next = new URLSearchParams(searchParams)
    next.delete("upgraded")
    setSearchParams(next, { replace: true })
  }

  const orderedPlans = PLAN_ORDER.map((n) => plans.find((p) => p.name === n))
    .filter((p): p is PlanRow => !!p)

  async function onUpgrade(planName: string, seats: number) {
    const enumKey = CHECKOUT_KEY[planName]
    if (!enumKey || enumKey === "free") return
    setUpgradingPlan(planName)
    try {
      const res = await postCheckoutSession(enumKey, seats, cycle)
      window.location.href = res.checkout_url
    } catch (err) {
      setUpgradingPlan(null)
      const detail = err instanceof Error ? err.message : String(err)
      toast.error("Couldn't start checkout", detail)
    }
  }

  const [openingPortal, setOpeningPortal] = useState(false)
  async function onManage(
    targetPlan?: "pro" | "team" | "cancel",
    targetCycle: "monthly" | "annual" = "monthly",
  ) {
    setOpeningPortal(true)
    try {
      const returnUrl = `${window.location.origin}/settings/billing`
      const res = await postPortalSession(returnUrl, targetPlan, targetCycle)
      // Stripe portal opens in the current tab — customer portal is a
      // short-lived URL, opening in a new window risks the session
      // expiring or the user losing context.
      window.location.href = res.portal_url
    } catch (err) {
      setOpeningPortal(false)
      const detail = err instanceof Error ? err.message : String(err)
      toast.error("Couldn't open billing portal", detail)
    }
  }

  function refreshNow() {
    void queryClient.invalidateQueries({ queryKey: ["me", "subscription"] })
  }

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Settings
        </p>
        <h1 className="mt-2 font-sans text-3xl font-semibold text-ink">Billing</h1>
        <p className="mt-2 text-sm text-ink-muted">
          Pick a plan that matches your team size and retention needs.
        </p>
      </div>

      {banner && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center gap-3 rounded border border-op-create/40 bg-op-create/5 px-4 py-3"
        >
          <span className="inline-flex h-2 w-2 rounded-full bg-op-create" aria-hidden="true" />
          <p className="flex-1 text-sm text-ink">
            Checkout complete — your new plan will appear in a few seconds.
          </p>
          <button
            type="button"
            onClick={refreshNow}
            className="rounded-sm border border-rule px-2 py-1 font-mono text-caption text-ink hover:bg-sunken"
          >
            Refresh
          </button>
        </div>
      )}

      {mockBanner && (
        <div
          role="status"
          className="flex items-start gap-3 rounded border border-flag-migration/40 bg-flag-migration/5 px-4 py-3"
        >
          <span className="mt-0.5 inline-flex font-mono text-micro font-semibold uppercase tracking-wider text-flag-migration">
            Demo mode
          </span>
          <p className="flex-1 text-sm text-ink">
            This is a demo environment — Stripe checkout isn't live. In
            production you would have been redirected to the{" "}
            <strong className="font-semibold">{mockPlan}</strong> plan
            checkout.
          </p>
          <button
            type="button"
            onClick={() => {
              setMockBanner(false)
              const next = new URLSearchParams(searchParams)
              next.delete("mock")
              next.delete("plan")
              setSearchParams(next, { replace: true })
            }}
            aria-label="Dismiss"
            className="-m-1 p-1 text-ink-faint hover:text-ink"
          >
            ×
          </button>
        </div>
      )}

      <section className="rounded border border-rule bg-surface p-6">
        <p className="font-mono text-caption uppercase tracking-wider text-ink-faint">
          Current plan
        </p>
        {subLoading ? (
          <div className="mt-3 h-6 w-32 rounded-sm bg-sunken/60 motion-safe:animate-pulse" />
        ) : (
          <div className="mt-3 flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-3xl font-semibold text-ink">
                {currentPlanName}
              </span>
              {subscription?.status && (
                <span className="font-mono text-caption uppercase tracking-wider text-ink-muted">
                  · {subscription.status}
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {currentPlanName !== "Free" && (
                subscription?.stripe_customer_id ? (
                  <button
                    type="button"
                    onClick={() => onManage()}
                    disabled={openingPortal}
                    className="inline-flex min-h-[36px] items-center gap-2 rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 disabled:opacity-60"
                  >
                    {openingPortal ? "Opening…" : "Manage subscription"}
                    {!openingPortal && <span aria-hidden="true">↗</span>}
                  </button>
                ) : (
                  <span
                    className="inline-flex min-h-[36px] items-center rounded-sm border border-dashed border-flag-migration/60 bg-flag-migration/5 px-3 py-1 font-mono text-caption text-ink"
                    title="Your subscription was set outside of Stripe Checkout. Re-checkout below to sync and unlock Manage."
                  >
                    Not synced with Stripe
                  </span>
                )
              )}
              <a
                href="#plans"
                className="inline-flex min-h-[36px] items-center rounded-sm bg-accent-500 px-3 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600"
              >
                {currentPlanName === "Free" ? "Upgrade →" : "Change plan →"}
              </a>
            </div>
          </div>
        )}
      </section>

      <div
        id="plans"
        role="radiogroup"
        aria-label="Billing cycle"
        className="inline-flex scroll-mt-6 rounded-sm border border-rule bg-surface p-0.5 font-mono text-caption"
      >
        {(["monthly","annual"] as const).map((c) => {
          const active = cycle === c
          return (
            <button
              key={c}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => setCycle(c)}
              className={
                "min-h-[36px] rounded-sm px-4 py-1 uppercase tracking-wider transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
                (active ? "bg-ink text-paper" : "text-ink-muted hover:text-ink")
              }
            >
              {c === "monthly" ? "Monthly" : "Annual"}
              {c === "annual" && (
                <span className="ml-2 font-mono text-micro font-semibold text-accent-500">−21%</span>
              )}
            </button>
          )
        })}
      </div>

      {plansLoading ? (
        <p className="text-sm text-ink-muted">Loading plans…</p>
      ) : orderedPlans.length === 0 ? (
        <p className="text-sm text-ink-muted">No plans available.</p>
      ) : (
        <section aria-label="Plans" className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {orderedPlans.map((p) => (
            <PlanCard
              key={p.id}
              plan={p}
              cycle={cycle}
              current={p.name === currentPlanName}
              currentPlanName={currentPlanName}
              hasStripeCustomer={Boolean(subscription?.stripe_customer_id)}
              busy={upgradingPlan === p.name}
              openingPortal={openingPortal}
              featureRows={featureRows}
              onUpgrade={(seats) => onUpgrade(p.name, seats)}
              onOpenPortal={onManage}
            />
          ))}
        </section>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-dashed border-rule pt-4">
        <p className="font-mono text-micro text-ink-faint">
          Secured by Stripe · cancel anytime · seats pro-rated
        </p>
        <a
          href={`${(import.meta.env.VITE_MARKETING_URL as string | undefined) ?? "https://yoru.sh"}/pricing`}
          target="_blank"
          rel="noopener noreferrer"
          className="font-mono text-caption text-ink-muted underline decoration-rule underline-offset-2 hover:text-ink"
        >
          Compare all features →
        </a>
      </div>
    </div>
  )
}

function PlanCard({
  plan,
  cycle,
  current,
  currentPlanName,
  hasStripeCustomer,
  busy,
  openingPortal,
  featureRows,
  onUpgrade,
  onOpenPortal,
}: {
  plan: PlanRow
  cycle: "monthly" | "annual"
  current: boolean
  currentPlanName: string
  hasStripeCustomer: boolean
  busy: boolean
  openingPortal: boolean
  featureRows: { key: string; label: string }[]
  onUpgrade: (seats: number) => void
  onOpenPortal: (
    targetPlan?: "pro" | "team" | "cancel",
    targetCycle?: "monthly" | "annual",
  ) => void
}) {
  const { data: features = {} } = useQuery({
    queryKey: ["plans", plan.id, "features"],
    queryFn: () => fetchPlanFeatures(plan.id),
    staleTime: 5 * 60_000,
    meta: { silent: true },
  })

  // Seat picker — Team only. Pro is a solo-dev flat price, Free is 1-seat
  // by definition, Org is sales-driven so no self-serve seat picker.
  const perSeat = plan.name === "Team"
  const seatsCap = extractSeatsCap(features["seats_max"])
  // seatsCap conventions: N (>0) = hard cap · -1 = unlimited · 0 = unknown.
  // Team's seats_max is the authoritative cap; default 200 if feature row
  // is missing so the picker doesn't lock at 1.
  const effectiveCap = seatsCap > 0 ? Math.max(seatsCap, 10) : 200
  const [seats, setSeats] = useState<number>(() => (plan.name === "Team" ? 5 : 1))
  const clampedSeats = perSeat ? Math.max(1, Math.min(seats, effectiveCap)) : 1

  return (
    <article
      className={
        "flex flex-col rounded border bg-surface p-5 " +
        (current ? "border-accent-500 ring-1 ring-accent-500/40" : "border-rule")
      }
    >
      <header className="pb-3">
        <div className="flex items-center justify-between">
          <h2 className="font-sans text-2xl font-semibold text-ink">{plan.name}</h2>
          {current && (
            <span className="rounded-sm bg-accent-500/10 px-2 py-0.5 font-mono text-micro font-semibold uppercase tracking-wider text-accent-500 ring-1 ring-inset ring-accent-500/40">
              Current
            </span>
          )}
        </div>
        {plan.description && (
          <p className="mt-1 text-caption text-ink-muted">{plan.description}</p>
        )}
      </header>
      <div className="border-t border-dashed border-rule py-3">
        <p className="font-mono text-xl font-semibold text-ink tabular-nums">
          {formatPrice(plan, cycle)}
          <span className="ml-1 font-mono text-caption font-normal text-ink-muted">
            {priceCadence(plan)}{cycle === "annual" ? " · billed annually" : ""}
          </span>
        </p>
      </div>
      <ul className="flex-1 space-y-1.5 border-t border-dashed border-rule pt-3 font-mono text-caption text-ink">
        {featureRows.map((f) => (
          <li key={f.key} className="flex items-baseline justify-between gap-2">
            <span className="text-ink-muted">{f.label}</span>
            <span className="tabular-nums">{formatFeatureValue(f.key, features[f.key])}</span>
          </li>
        ))}
      </ul>
      {!current && (() => {
        // Direction vs the user's current plan. Drives the button label and
        // whether we route to Stripe Checkout (new sub) or Customer Portal
        // (existing sub → upgrade/downgrade/cancel).
        const direction = tierCompare(plan.name, currentPlanName) // <0 down, >0 up
        const isUserOnPaid = currentPlanName !== "Free"
        const usePortal = isUserOnPaid && hasStripeCustomer

        // Org card → always contact sales (custom quote, not self-serve)
        if (plan.name === "Org") {
          return (
            <div className="mt-4">
              <a
                href="https://yoru.sh/contact-sales?from=dashboard"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-[40px] w-full items-center justify-center gap-2 rounded-sm border border-ink bg-ink px-3 py-2 font-mono text-caption font-semibold uppercase tracking-wider text-canvas hover:bg-ink-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
              >
                Contact sales →
              </a>
              <p className="mt-2 text-center font-mono text-micro text-ink-faint">
                SSO, SCIM, signed audits · we'll scope it with you
              </p>
            </div>
          )
        }

        // Org customers never self-serve tier changes — their subscription
        // is custom-priced (seats, terms) so any move (down or up) re-opens
        // the sales conversation.
        if (currentPlanName === "Org") {
          return (
            <div className="mt-4">
              <a
                href={`https://yoru.sh/contact-sales?from=dashboard`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-[40px] w-full items-center justify-center gap-2 rounded-sm border border-rule bg-surface px-3 py-2 font-mono text-caption uppercase tracking-wider text-ink hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
              >
                Contact sales →
              </a>
              <p className="mt-2 text-center font-mono text-micro text-ink-faint">
                Custom contract · we'll handle the switch
              </p>
            </div>
          )
        }

        // Free card → only shown as a downgrade option for paid users.
        // Routes to the Stripe portal where they cancel at period-end.
        if (plan.name === "Free") {
          if (!isUserOnPaid) return null
          return (
            <div className="mt-4">
              <button
                type="button"
                onClick={() => onOpenPortal("cancel")}
                disabled={openingPortal || !hasStripeCustomer}
                className="inline-flex min-h-[40px] w-full items-center justify-center gap-2 rounded-sm border border-rule bg-surface px-3 py-2 font-mono text-caption uppercase tracking-wider text-ink-muted hover:border-flag-env/60 hover:bg-flag-env/10 hover:text-flag-env-fg disabled:opacity-60"
              >
                {openingPortal ? "Opening…" : "Downgrade to Free"}
              </button>
            </div>
          )
        }

        // Pro / Team card action — depends on direction + whether the user
        // already has a Stripe customer.
        return (
          <div className="mt-4 space-y-2">
            {perSeat && !usePortal && (
              <label className="flex items-center justify-between gap-2 rounded-sm border border-rule bg-paper px-2 py-1.5">
                <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
                  Seats
                </span>
                <input
                  type="number"
                  min={1}
                  max={effectiveCap}
                  value={seats}
                  onChange={(e) =>
                    setSeats(Math.max(1, parseInt(e.target.value, 10) || 1))
                  }
                  className="w-16 rounded-sm border border-rule bg-paper px-2 py-0.5 text-right font-mono text-caption text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
                />
                <span className="shrink-0 font-mono text-caption tabular-nums text-ink">
                  = $
                  {computeBillingTotal(
                    plan.name,
                    Number(plan.price) || 0,
                    clampedSeats,
                    cycle,
                  ).toFixed(0)}
                  {cycle === "monthly" ? "/mo" : "/yr"}
                </span>
              </label>
            )}
            {usePortal ? (
              <>
                <button
                  type="button"
                  onClick={() =>
                    onOpenPortal(
                      plan.name.toLowerCase() as "pro" | "team",
                      cycle,
                    )
                  }
                  disabled={openingPortal}
                  className={
                    "inline-flex min-h-[40px] w-full items-center justify-center gap-2 rounded-sm px-3 py-2 font-mono text-caption font-semibold uppercase tracking-wider focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60 " +
                    (direction < 0
                      ? "border border-rule bg-surface text-ink-muted hover:border-flag-env/60 hover:bg-flag-env/10 hover:text-flag-env-fg"
                      : "bg-accent-500 text-paper hover:bg-accent-600")
                  }
                >
                  {openingPortal
                    ? "Opening…"
                    : direction < 0
                      ? `Downgrade to ${plan.name}`
                      : `Upgrade to ${plan.name}`}
                </button>
              </>
            ) : (
              <button
                type="button"
                onClick={() => onUpgrade(clampedSeats)}
                disabled={busy}
                className="inline-flex min-h-[40px] w-full items-center justify-center gap-2 rounded-sm bg-accent-500 px-3 py-2 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:opacity-60"
              >
                {busy && (
                  <svg
                    className="h-3 w-3 animate-spin"
                    viewBox="0 0 24 24"
                    fill="none"
                    aria-hidden="true"
                  >
                    <circle
                      cx="12"
                      cy="12"
                      r="10"
                      stroke="currentColor"
                      strokeOpacity="0.25"
                      strokeWidth="4"
                    />
                    <path
                      d="M22 12a10 10 0 0 1-10 10"
                      stroke="currentColor"
                      strokeWidth="4"
                      strokeLinecap="round"
                    />
                  </svg>
                )}
                {busy ? "Starting checkout…" : `Upgrade to ${plan.name}`}
              </button>
            )}
          </div>
        )
      })()}
    </article>
  )
}
