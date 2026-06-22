import { useEffect, useState, type FormEvent } from "react"
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom"
import { signin, AuthError } from "../lib/auth-api"
import { getSetupStatus } from "../lib/setup-api"
import { useSession } from "../auth/useSession"
import { GithubOAuthButton } from "../auth/GithubOAuthButton"

const INPUT_CLASS =
  "mt-1 w-full rounded border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"

const LABEL_CLASS = "font-mono text-micro uppercase tracking-wider text-ink-faint"

function friendlyError(err: unknown): string {
  if (err instanceof AuthError) {
    if (err.status === 401) return "Invalid email or password."
    if (err.status === 429) return "Too many requests. Wait a minute, then try again."
    return err.detail || `Sign-in failed (${err.status}).`
  }
  return "Something went wrong. Please try again."
}

export function SignInPage() {
  const { user, loading, refresh } = useSession()
  const location = useLocation() as { state?: { from?: string } }
  const from = location.state?.from ?? null
  const [searchParams] = useSearchParams()
  const reason = searchParams.get("reason")
  const tokenExpired = reason === "token-expired"
  const oauthBanner = (() => {
    if (reason === "oauth-failed") return "GitHub sign-in failed. Please try again or use your email."
    if (reason === "oauth-refused") return "Sign-in cancelled on GitHub."
    return null
  })()

  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [err, setErr] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Fresh self-hosted instance with no admin yet → send the first visitor to
  // the onboarding wizard instead of an unusable sign-in form.
  const [needsSetup, setNeedsSetup] = useState(false)
  useEffect(() => {
    getSetupStatus()
      .then((s) => setNeedsSetup(s.needs_setup))
      .catch(() => {
        /* API unreachable — leave the form up; the error surfaces on submit. */
      })
  }, [])

  if (loading) return null
  if (needsSetup) return <Navigate to="/setup" replace />
  if (user) return <Navigate to={from ?? "/"} replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setErr(null)
    setSubmitting(true)
    try {
      await signin(email, password)
      await refresh()
    } catch (error) {
      setErr(friendlyError(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-paper px-4">
      <div className="w-full max-w-sm rounded border border-rule bg-surface p-6 shadow-sm">
        <div className="flex items-center gap-3">
          <img src="/yoru-mark.png" alt="" aria-hidden="true" className="h-8 w-8" />
          <h1 className="font-mono text-2xl font-semibold tracking-tight text-ink">yoru</h1>
        </div>
        <p className="mt-2 font-mono text-micro uppercase tracking-wider text-ink-faint">
          Audit receipts · AI coding sessions
        </p>

        {tokenExpired && (
          <div
            role="status"
            className="mt-5 border-l-2 border-accent-500 bg-sunken px-3 py-2 text-sm text-ink"
          >
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Session expired
            </span>
            <p className="mt-0.5">Sign in again to continue.</p>
          </div>
        )}

        {oauthBanner && (
          <div
            role="alert"
            className="mt-5 border-l-2 border-flag-env bg-flag-env/5 px-3 py-2 text-sm text-ink"
          >
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              [oauth]
            </span>
            <p className="mt-0.5">{oauthBanner}</p>
          </div>
        )}

        <div className="mt-6">
          <GithubOAuthButton />
        </div>

        <div className="my-5 flex items-center gap-3">
          <span className="h-px flex-1 bg-rule" aria-hidden="true" />
          <span className="font-mono text-micro uppercase tracking-wider text-ink-faint">or</span>
          <span className="h-px flex-1 bg-rule" aria-hidden="true" />
        </div>

        <form onSubmit={onSubmit} className="space-y-3">
          <label className="block">
            <span className={LABEL_CLASS}>Email</span>
            <input
              type="email"
              required
              autoFocus
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={INPUT_CLASS}
              placeholder="you@company.dev"
            />
          </label>
          <label className="block">
            <span className={LABEL_CLASS}>Password</span>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={INPUT_CLASS}
              placeholder="••••••••"
            />
          </label>
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded bg-accent-500 px-3 py-2 text-sm font-medium text-primary-950 hover:bg-accent-400 outline-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper disabled:opacity-50"
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
          {err && (
            <div
              role="alert"
              className="border-l-2 border-flag-env bg-flag-env/5 px-3 py-2 text-sm text-ink"
            >
              <span className="font-mono text-ink-muted">[ERR]</span> {err}
            </div>
          )}
        </form>

        <hr className="my-5 border-dashed border-rule" />

        <p className="font-sans text-sm text-ink-muted">
          No account yet?{" "}
          <Link
            to="/signup"
            className="font-medium text-accent-600 hover:text-accent-500 underline-offset-2 hover:underline"
          >
            Create one →
          </Link>
        </p>
      </div>
    </div>
  )
}
