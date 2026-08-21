import { useEffect, useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { apiFetch, ApiError } from "../lib/api"

// Distinct failure reasons the backend's /device-code/approve can produce
// (auth_router.py device_code_approve): 404 unknown code, 410 expired,
// 409 code already in a non-pending status (approved/consumed/denied — i.e.
// already paired), 429 reserved for when rate-limiting re-lands (issue #54,
// currently unreachable but handled so the UI doesn't need a follow-up
// change once it does). Anything else is "unknown" — a real, unclassified
// failure (network down, 5xx) whose raw message is still worth showing.
type ErrorReason = "invalid" | "expired" | "already-paired" | "rate-limited" | "unknown"

type State =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success"; code: string }
  | { kind: "error"; reason: ErrorReason; message: string }

const ERROR_COPY: Record<ErrorReason, { headline: string; hint: React.ReactNode }> = {
  invalid: {
    headline: "That code doesn't match a pending pairing.",
    hint: (
      <>
        Double-check the code your terminal printed, or run{" "}
        <code className="font-mono text-ink">yoru init</code> again to get a new one.
      </>
    ),
  },
  expired: {
    headline: "This code has expired.",
    hint: (
      <>
        Code may be expired. Re-run <code className="font-mono text-ink">yoru init</code> and
        try again.
      </>
    ),
  },
  "already-paired": {
    headline: "This code was already used to pair a device.",
    hint: (
      <>
        If that wasn't you, revoke it from{" "}
        <Link
          to="/settings/tokens"
          className="text-accent-600 underline-offset-2 hover:text-accent-500 hover:underline"
        >
          Settings → Tokens
        </Link>
        .
      </>
    ),
  },
  "rate-limited": {
    headline: "Too many attempts.",
    hint: "Wait a moment, then try again.",
  },
  unknown: {
    headline: "",
    hint: null,
  },
}

function classifyError(err: unknown): { reason: ErrorReason; message: string } {
  if (err instanceof ApiError) {
    switch (err.status) {
      case 404:
        return { reason: "invalid", message: ERROR_COPY.invalid.headline }
      case 410:
        return { reason: "expired", message: ERROR_COPY.expired.headline }
      case 409:
        return { reason: "already-paired", message: ERROR_COPY["already-paired"].headline }
      case 429:
        return { reason: "rate-limited", message: ERROR_COPY["rate-limited"].headline }
    }
  }
  return {
    reason: "unknown",
    message: err instanceof Error ? err.message : "Could not authorize",
  }
}

function formatCode(raw: string): string {
  const clean = raw.toUpperCase().replace(/[^A-Z0-9]/g, "")
  if (clean.length <= 4) return clean
  return `${clean.slice(0, 4)}-${clean.slice(4, 8)}`
}

/** Card-wide input. Large mono, wide tracking — the code has to *look* like a
 *  receipt stub, not a generic form field. */
const INPUT_BASE =
  "mt-2 w-full rounded border border-rule bg-paper px-4 py-4 text-center font-mono text-3xl " +
  "tracking-[0.4em] text-ink outline-none " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
  "disabled:opacity-60 disabled:cursor-not-allowed " +
  "placeholder:text-ink-faint placeholder:tracking-[0.4em]"

const LABEL_CLASS = "block font-mono text-micro uppercase tracking-wider text-ink-faint"

/** Inline spinner — only rendered during the submitting state. Sized to align
 *  with the button label's cap height. */
function Spinner() {
  return (
    <svg
      className="h-3.5 w-3.5 motion-safe:animate-spin"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  )
}

export function PairCliPage() {
  const [params] = useSearchParams()
  const initialCode = useMemo(() => formatCode(params.get("code") ?? ""), [params])
  const [code, setCode] = useState(initialCode)
  const [state, setState] = useState<State>({ kind: "idle" })

  useEffect(() => {
    setCode(initialCode)
  }, [initialCode])

  const submitting = state.kind === "submitting"
  const showFormatHint = code.length > 0 && !/^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code)
  const canSubmit = /^[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(code) && !submitting

  async function authorize(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    setState({ kind: "submitting" })
    try {
      await apiFetch<void>("/auth/device-code/approve", {
        method: "POST",
        body: JSON.stringify({ user_code: code }),
      })
      setState({ kind: "success", code })
    } catch (err) {
      setState({ kind: "error", ...classifyError(err) })
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-paper px-4 py-10">
      <div className="w-full max-w-sm rounded border border-rule bg-surface p-6 shadow-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <img src="/yoru-mark.png" alt="" aria-hidden="true" className="h-7 w-7" />
            <h1 className="font-mono text-2xl font-semibold tracking-tight text-ink">
              yoru
            </h1>
          </div>
          <span className="font-mono text-micro uppercase tracking-wider text-ink-faint">
            § pair a device
          </span>
        </div>

        {state.kind === "success" ? (
          <div
            role="status"
            aria-live="polite"
            className="mt-6 border-l-2 border-accent-500 bg-sunken px-4 py-4"
          >
            <div className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="font-mono text-base leading-none text-accent-500"
              >
                ✓
              </span>
              <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">
                device authorized
              </p>
            </div>
            <p className="mt-2 text-sm text-ink">
              Your CLI will finish setup in a few seconds. You can close this tab.
            </p>
            <p className="mt-3 font-mono text-micro text-ink-faint">
              code{" "}
              <code className="font-mono text-ink">{state.code}</code>{" "}
              consumed — no longer valid
            </p>
          </div>
        ) : (
          <>
            <p className="mt-5 text-sm leading-relaxed text-ink-muted">
              You ran{" "}
              <code className="rounded-sm border border-rule bg-paper px-1.5 py-0.5 font-mono text-caption text-ink">
                yoru init
              </code>{" "}
              on a machine. Confirm the code your terminal printed, then authorize.
            </p>

            <form onSubmit={authorize} className="mt-6 space-y-4" noValidate>
              <div>
                <label htmlFor="pair-code" className={LABEL_CLASS}>
                  Pairing code
                </label>
                <input
                  id="pair-code"
                  autoFocus
                  autoComplete="one-time-code"
                  inputMode="text"
                  spellCheck={false}
                  value={code}
                  disabled={submitting}
                  onChange={(e) => setCode(formatCode(e.target.value))}
                  placeholder="ABCD-EFGH"
                  maxLength={9}
                  className={INPUT_BASE}
                  aria-describedby="pair-hint"
                  aria-invalid={state.kind === "error" || showFormatHint ? "true" : undefined}
                />
                <p
                  id="pair-hint"
                  className={
                    "mt-2 font-mono text-micro " +
                    (showFormatHint ? "text-flag-env" : "text-ink-faint")
                  }
                >
                  {showFormatHint
                    ? "Format is 4 letters, dash, 4 letters (e.g. ABCD-EFGH)."
                    : "Must match the code printed by your terminal, character for character."}
                </p>
              </div>

              <button
                type="submit"
                disabled={!canSubmit}
                className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded bg-accent-500 px-3 py-2 text-sm font-medium text-primary-950 hover:bg-accent-400 outline-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {submitting && <Spinner />}
                {submitting ? "Authorizing…" : "Authorize this device"}
              </button>

              {state.kind === "error" && (
                <div
                  role="alert"
                  aria-live="polite"
                  data-error-reason={state.reason}
                  className="border-l-2 border-flag-env bg-flag-env/5 px-3 py-2 text-sm text-ink"
                >
                  <span className="font-mono text-ink-muted">[ERR]</span> {state.message}
                  {ERROR_COPY[state.reason].hint && (
                    <p className="mt-1 font-mono text-micro text-ink-faint">
                      {ERROR_COPY[state.reason].hint}
                    </p>
                  )}
                </div>
              )}
            </form>

            <hr className="my-5 border-dashed border-rule" />

            <div className="space-y-2">
              <p
                role="note"
                className="border-l-2 border-flag-env/40 bg-flag-env/5 px-3 py-2 font-mono text-micro leading-relaxed text-ink"
              >
                <span className="font-mono text-ink-muted">[safety]</span>{" "}
                If you didn&apos;t run{" "}
                <code className="font-mono text-ink">yoru init</code>, close this tab —
                your account stays safe.
              </p>
              <p className="font-mono text-micro leading-relaxed text-ink-faint">
                Authorized machines stream events until you revoke them from{" "}
                <Link
                  to="/settings/tokens"
                  className="text-accent-600 underline-offset-2 hover:text-accent-500 hover:underline"
                >
                  Settings → Tokens
                </Link>
                .
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
