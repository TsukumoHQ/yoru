import { useEffect, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { connectBitbucket } from "../lib/api"

/**
 * Receives the Supabase Bitbucket OAuth redirect. Supabase places tokens in
 * the URL hash fragment (not query params): #access_token=…&provider_token=…
 *
 * We extract provider_token, POST it to /me/bitbucket/connect (the existing
 * dashboard cookie session auths the call), then redirect to the workspace
 * repo mapping page. Twin of GithubCallbackPage.
 */
export function BitbucketCallbackPage() {
  const navigate = useNavigate()
  const [state, setState] = useState<
    | { kind: "running" }
    | { kind: "success"; login: string }
    | { kind: "error"; message: string }
  >({ kind: "running" })
  const ranRef = useRef(false)

  useEffect(() => {
    if (ranRef.current) return
    ranRef.current = true

    const hash = window.location.hash.replace(/^#/, "")
    const params = new URLSearchParams(hash)
    const providerToken =
      params.get("provider_token") ?? params.get("access_token") ?? ""
    if (!providerToken) {
      setState({
        kind: "error",
        message: "No token in the redirect — did Bitbucket deny the request?",
      })
      return
    }

    ;(async () => {
      try {
        const resp = await connectBitbucket({ provider_token: providerToken })
        setState({ kind: "success", login: resp.bitbucket_username ?? "" })
        // Clean the hash so a refresh doesn't try to reuse the token.
        window.history.replaceState(null, "", window.location.pathname)
        setTimeout(() => navigate("/settings/workspaces", { replace: true }), 1200)
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Connection failed"
        setState({ kind: "error", message: msg })
      }
    })()
  }, [navigate])

  return (
    <div className="mx-auto w-full max-w-lg px-6 py-16">
      <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">
        Bitbucket
      </p>
      <h1 className="mt-2 font-serif text-h2 text-ink">
        {state.kind === "running"
          ? "Connecting to Bitbucket…"
          : state.kind === "success"
            ? "Connected"
            : "Couldn't connect"}
      </h1>

      {state.kind === "success" && (
        <p className="mt-4 text-sm text-ink">
          Signed in as <strong>@{state.login || "bitbucket user"}</strong>. Taking
          you back to workspaces…
        </p>
      )}
      {state.kind === "error" && (
        <div className="mt-4 space-y-3 text-sm">
          <p className="text-ink">{state.message}</p>
          <div className="flex gap-2">
            <Link
              to="/settings/workspaces"
              className="rounded border border-rule px-3 py-1.5 font-mono text-caption text-ink hover:bg-sunken"
            >
              Back to workspaces
            </Link>
            <button
              type="button"
              onClick={() => {
                ranRef.current = false
                setState({ kind: "running" })
              }}
              className="rounded border border-ink bg-ink px-3 py-1.5 font-mono text-caption uppercase tracking-wider text-canvas hover:bg-ink-muted"
            >
              Retry
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
