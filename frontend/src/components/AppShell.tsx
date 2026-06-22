import { useState } from "react"
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { useSession } from "../auth/useSession"
import { UpgradeBanner } from "../features/billing/UpgradeBanner"
import { NotificationBell } from "../features/notifications/NotificationBell"
import { apiFetch, ApiError } from "../lib/api"
import { useInstanceConfig } from "../lib/config"

type NavItem = { to: string; label: string; end?: boolean; billingOnly?: boolean }

const NAV: NavItem[] = [
  { to: "/",                        label: "/sessions",       end: true },
  { to: "/settings/workspaces",     label: "/workspaces" },
  { to: "/settings/teams",          label: "/teams" },
  { to: "/settings/tokens",         label: "/tokens" },
  { to: "/settings/billing",        label: "/billing", billingOnly: true },
  { to: "/settings/profile",        label: "/profile" },
]

function navLinkClass({ isActive }: { isActive: boolean }) {
  return (
    "rounded-sm font-mono text-caption transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface " +
    (isActive ? "text-ink" : "text-ink-muted hover:text-ink")
  )
}

interface MeSubscription {
  plan_name?: string
  status?: string
}

async function fetchPlanBadge(): Promise<string> {
  try {
    const sub = await apiFetch<MeSubscription | null>("/me/subscription")
    return (sub?.plan_name ?? "Free").toLowerCase()
  } catch (err) {
    // Silently fall back — backend may still be seeding the Free subscription.
    if (err instanceof ApiError) return "free"
    return "free"
  }
}

const PLAN_STYLE: Record<string, string> = {
  free: "bg-sunken text-ink-muted ring-rule",
  pro:  "bg-accent-500/10 text-accent-500 ring-accent-500/40",
  team: "bg-accent-500/20 text-accent-500 ring-accent-500/60 font-semibold",
  org:  "bg-ink text-paper ring-ink font-semibold",
}

function PlanBadge({ plan }: { plan: string }) {
  const style = PLAN_STYLE[plan] ?? PLAN_STYLE.free
  return (
    <Link
      to="/settings/billing"
      title={`Plan · ${plan}`}
      className={
        "inline-flex items-center rounded-sm px-2 py-0.5 font-mono text-micro font-semibold uppercase tracking-wider ring-1 ring-inset focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-surface " +
        style
      }
    >
      {plan}
    </Link>
  )
}

export function AppShell() {
  const { user, signOut } = useSession()
  const navigate = useNavigate()
  const [signingOut, setSigningOut] = useState(false)
  const config = useInstanceConfig()

  // On a self-hosted instance (billing disabled) hide billing-only nav.
  const navItems = NAV.filter((item) => config.billing_enabled || !item.billingOnly)

  const { data: plan = "free" } = useQuery({
    queryKey: ["me", "subscription", "plan"],
    queryFn: fetchPlanBadge,
    enabled: !!user && config.billing_enabled,
    staleTime: 60_000,
    retry: 0,
    meta: { silent: true },
  })

  async function onSignOut() {
    if (signingOut) return
    setSigningOut(true)
    try {
      await signOut()
      navigate("/signin", { replace: true })
    } finally {
      setSigningOut(false)
    }
  }

  return (
    <div className="min-h-screen bg-paper text-ink">
      <a
        href="#main"
        className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:left-3 focus-visible:top-3 focus-visible:z-50 focus-visible:rounded-sm focus-visible:border focus-visible:border-rule focus-visible:bg-surface focus-visible:px-3 focus-visible:py-2 focus-visible:font-mono focus-visible:text-caption focus-visible:text-ink focus-visible:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
      >
        Skip to content
      </a>
      <header className="border-b border-rule bg-surface">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
          <div className="flex items-center gap-6">
            <Link
              to="/"
              className="inline-flex items-center gap-2 rounded-sm font-mono text-sm font-semibold tracking-tight text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
            >
              <img src="/yoru-mark.png" alt="" aria-hidden="true" className="h-5 w-5" />
              yoru
            </Link>
            <nav aria-label="Primary" className="hidden items-center gap-5 md:flex">
              {navItems.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClass}>
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            {config.billing_enabled && <PlanBadge plan={plan} />}
            <NotificationBell />
            <span className="hidden text-caption text-ink-muted md:inline">{user?.email}</span>
            <button
              type="button"
              onClick={() => { void onSignOut() }}
              disabled={signingOut}
              className="inline-flex items-center gap-1.5 rounded border border-rule px-2 py-1 text-caption text-ink-muted hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 disabled:opacity-60"
            >
              {signingOut && (
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
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-6xl px-6 py-6">
        {config.billing_enabled && <UpgradeBanner />}
        <Outlet />
      </main>
    </div>
  )
}
