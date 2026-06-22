import { useEffect, useState, type FormEvent } from "react"
import { Navigate } from "react-router-dom"
import {
  getSetupStatus,
  testDatabase,
  initInstance,
  type SetupStatus,
} from "../lib/setup-api"

const INPUT_CLASS =
  "mt-1 w-full rounded border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"

const LABEL_CLASS = "font-mono text-micro uppercase tracking-wider text-ink-faint"

type DbChoice = "bundled" | "custom"

export function SetupPage() {
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  const [dbChoice, setDbChoice] = useState<DbChoice>("bundled")
  const [dbUrl, setDbUrl] = useState("")
  const [dbTest, setDbTest] = useState<{ ok: boolean; msg: string } | null>(null)
  const [testing, setTesting] = useState(false)

  const [email, setEmail] = useState("")
  const [firstName, setFirstName] = useState("")
  const [password, setPassword] = useState("")
  const [setupToken, setSetupToken] = useState("")

  const [err, setErr] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [done, setDone] = useState<{ restart: boolean } | null>(null)

  useEffect(() => {
    getSetupStatus().then(setStatus).catch((e) => setLoadErr(String(e.message ?? e)))
  }, [])

  if (loadErr) {
    return (
      <Shell>
        <div role="alert" className="border-l-2 border-flag-env bg-flag-env/5 px-3 py-2 text-sm text-ink">
          <span className="font-mono text-ink-muted">[ERR]</span> Cannot reach the API: {loadErr}
        </div>
      </Shell>
    )
  }
  if (!status) return null
  // Already installed → nothing to do here.
  if (status.installed) return <Navigate to="/signin" replace />

  async function onTestDb() {
    setTesting(true)
    setDbTest(null)
    try {
      const r = await testDatabase(dbUrl.trim())
      setDbTest(r.ok ? { ok: true, msg: `Connected to ${r.url}` } : { ok: false, msg: r.error ?? "Failed" })
    } catch (e) {
      setDbTest({ ok: false, msg: String((e as Error).message) })
    } finally {
      setTesting(false)
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setErr(null)
    if (dbChoice === "custom" && !dbTest?.ok) {
      setErr("Test the database connection first.")
      return
    }
    setSubmitting(true)
    try {
      const r = await initInstance({
        admin_email: email,
        admin_password: password,
        first_name: firstName || undefined,
        database_url: dbChoice === "custom" ? dbUrl.trim() : undefined,
        setup_token: status?.setup_token_required ? setupToken : undefined,
      })
      setDone({ restart: r.restart_required })
    } catch (e) {
      setErr(String((e as Error).message))
    } finally {
      setSubmitting(false)
    }
  }

  if (done) {
    return (
      <Shell>
        <div className="border-l-2 border-accent-500 bg-sunken px-3 py-3 text-sm text-ink">
          <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">Done</span>
          <p className="mt-1">Admin account created.</p>
          {done.restart ? (
            <p className="mt-2 text-ink-muted">
              You switched databases — restart the API, then sign in.
            </p>
          ) : (
            <p className="mt-2">
              <a href="/signin" className="font-medium text-accent-600 underline-offset-2 hover:underline">
                Go to sign in →
              </a>
            </p>
          )}
        </div>
      </Shell>
    )
  }

  return (
    <Shell>
      <p className="mt-2 font-mono text-micro uppercase tracking-wider text-ink-faint">
        First-run setup · create your admin account
      </p>

      <form onSubmit={onSubmit} className="mt-6 space-y-5">
        {/* ── database ── */}
        <fieldset className="space-y-2">
          <legend className={LABEL_CLASS}>Database</legend>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="radio"
              name="db"
              checked={dbChoice === "bundled"}
              onChange={() => setDbChoice("bundled")}
            />
            Bundled SQLite (zero-config)
          </label>
          <label className="flex items-center gap-2 text-sm text-ink">
            <input
              type="radio"
              name="db"
              checked={dbChoice === "custom"}
              onChange={() => setDbChoice("custom")}
            />
            Existing database (Postgres URL)
          </label>
          {dbChoice === "custom" && (
            <div className="space-y-2 pl-6">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={dbUrl}
                  onChange={(e) => {
                    setDbUrl(e.target.value)
                    setDbTest(null)
                  }}
                  className={INPUT_CLASS + " font-mono"}
                  placeholder="postgres://user:pass@host:5432/dbname"
                />
                <button
                  type="button"
                  onClick={onTestDb}
                  disabled={testing || !dbUrl.trim()}
                  className="mt-1 shrink-0 rounded border border-rule px-3 py-2 text-sm text-ink hover:bg-sunken disabled:opacity-50"
                >
                  {testing ? "Testing…" : "Test"}
                </button>
              </div>
              {dbTest && (
                <p className={`text-sm ${dbTest.ok ? "text-accent-600" : "text-flag-env"}`}>
                  {dbTest.ok ? "✓ " : "✗ "}
                  {dbTest.msg}
                </p>
              )}
            </div>
          )}
        </fieldset>

        {/* ── admin ── */}
        <div className="space-y-3 border-t border-dashed border-rule pt-4">
          <label className="block">
            <span className={LABEL_CLASS}>Admin email</span>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={INPUT_CLASS}
              placeholder="you@company.dev"
            />
          </label>
          <label className="block">
            <span className={LABEL_CLASS}>First name (optional)</span>
            <input
              type="text"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              className={INPUT_CLASS}
            />
          </label>
          <label className="block">
            <span className={LABEL_CLASS}>Password (min 8 chars)</span>
            <input
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={INPUT_CLASS}
              placeholder="••••••••"
            />
          </label>
          {status.setup_token_required && (
            <label className="block">
              <span className={LABEL_CLASS}>Setup token</span>
              <input
                type="text"
                required
                value={setupToken}
                onChange={(e) => setSetupToken(e.target.value)}
                className={INPUT_CLASS + " font-mono"}
                placeholder="from the server console"
              />
            </label>
          )}
        </div>

        <button
          type="submit"
          disabled={submitting}
          className="w-full rounded bg-accent-500 px-3 py-2 text-sm font-medium text-primary-950 hover:bg-accent-400 outline-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper disabled:opacity-50"
        >
          {submitting ? "Creating…" : "Create admin & finish"}
        </button>
        {err && (
          <div role="alert" className="border-l-2 border-flag-env bg-flag-env/5 px-3 py-2 text-sm text-ink">
            <span className="font-mono text-ink-muted">[ERR]</span> {err}
          </div>
        )}
      </form>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-paper px-4 py-10">
      <div className="w-full max-w-md rounded border border-rule bg-surface p-6 shadow-sm">
        <div className="flex items-center gap-3">
          <img src="/yoru-mark.png" alt="" aria-hidden="true" className="h-8 w-8" />
          <h1 className="font-mono text-2xl font-semibold tracking-tight text-ink">yoru</h1>
        </div>
        {children}
      </div>
    </div>
  )
}
