import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ApiError,
  apiFetch,
  createServiceToken,
  listMyTokens,
  listServiceTokens,
  revokeMyToken,
  revokeServiceToken,
  type ServiceTokenItem,
  type UserTokenItem,
} from "../lib/api"
import { toast } from "../components/Toaster"

// Two tabs open on the same token list can both fire a revoke for the same
// row before either refetches — one wins, the other loses the race. The
// backend's revoke is idempotent (204 either way) so this rarely surfaces
// today, but treat 404/409/410 defensively as "someone already revoked it"
// rather than a raw backend error string: it's an expected outcome of the
// race, not a failure the operator needs to act on.
function isAlreadyRevokedError(err: unknown): boolean {
  return err instanceof ApiError && [404, 409, 410].includes(err.status)
}

function reportRevokeOutcome(err: unknown) {
  if (isAlreadyRevokedError(err)) {
    toast.info("Already revoked", "Another tab or session revoked this token first.")
    return
  }
  toast.error(err instanceof Error ? err.message : "Failed to revoke token")
}

interface Organization {
  id: string
  name: string
  slug: string
  type: "personal" | "team"
}

interface OrgListResponse {
  items: Organization[]
}

const MY_TOKENS_KEY = ["cli-tokens", "mine"] as const
const SERVICE_TOKENS_KEY = (orgId: string) =>
  ["cli-tokens", "service", orgId] as const
const ORGS_KEY = ["me", "organizations"] as const

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return ""
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const diff = Date.now() - d.getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return "just now"
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return d.toLocaleDateString()
}

function tokenLabel(label: string | null | undefined): string {
  return label?.trim() || "(no label)"
}

export function TokensPage() {
  const qc = useQueryClient()
  const [showRevoked, setShowRevoked] = useState(false)

  const { data: myTokens = [], isLoading: loadingMine } = useQuery({
    queryKey: MY_TOKENS_KEY,
    queryFn: listMyTokens,
  })

  const { data: orgsResp } = useQuery({
    queryKey: ORGS_KEY,
    queryFn: () => apiFetch<OrgListResponse>("/me/organizations"),
  })
  const orgs = useMemo(
    () => (orgsResp?.items ?? []).filter((o) => o.type === "team"),
    [orgsResp],
  )
  const [selectedOrg, setSelectedOrg] = useState<string>("")
  const activeOrg = selectedOrg || orgs[0]?.id || ""

  const revokeMine = useMutation({
    mutationFn: revokeMyToken,
    onSuccess: () => {
      toast.success("Token revoked")
      qc.invalidateQueries({ queryKey: MY_TOKENS_KEY })
    },
    onError: (e) => {
      reportRevokeOutcome(e)
      // A losing race still means the token is gone — refetch so the row
      // drops out of the active list instead of leaving a stale Revoke button.
      if (isAlreadyRevokedError(e)) qc.invalidateQueries({ queryKey: MY_TOKENS_KEY })
    },
  })

  const visibleMyTokens = showRevoked
    ? myTokens
    : myTokens.filter((t) => !t.revoked_at)
  const hiddenRevoked = myTokens.length - visibleMyTokens.length

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Settings
        </p>
        <h1 className="mt-2 font-sans text-3xl font-semibold text-ink">
          CLI tokens
        </h1>
        <p className="mt-2 text-sm text-ink-muted">
          Authenticate the Yoru CLI from your machines, CI runners, or
          servers. Personal tokens are per-human; service tokens survive team
          changes.
        </p>
      </div>

      {/* My machines */}
      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
            My machines · {visibleMyTokens.length}
          </h2>
          {hiddenRevoked > 0 && (
            <button
              type="button"
              onClick={() => setShowRevoked(true)}
              className="font-mono text-micro uppercase tracking-wider text-ink-faint hover:text-ink"
            >
              + {hiddenRevoked} revoked
            </button>
          )}
          {showRevoked && (
            <button
              type="button"
              onClick={() => setShowRevoked(false)}
              className="font-mono text-micro uppercase tracking-wider text-ink-faint hover:text-ink"
            >
              hide revoked
            </button>
          )}
        </div>

        {loadingMine ? (
          <p className="px-4 py-3 text-caption text-ink-muted">Loading…</p>
        ) : visibleMyTokens.length === 0 ? (
          <EmptyCard
            title="No personal tokens yet"
            body="Run `yoru init` in your terminal to pair this machine."
            cta={
              <Link
                to="/docs/install"
                className="rounded-sm border border-rule px-3 py-1.5 font-mono text-caption text-ink hover:bg-sunken"
              >
                Install guide →
              </Link>
            }
          />
        ) : (
          <TokenList
            tokens={visibleMyTokens}
            onRevoke={(id) => {
              if (
                confirm(
                  "Revoke this token? The CLI on that machine will need to re-pair.",
                )
              ) {
                revokeMine.mutate(id)
              }
            }}
            revokingId={revokeMine.isPending ? revokeMine.variables : null}
          />
        )}
      </section>

      {/* Service tokens */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
            Service tokens
          </h2>
          {orgs.length > 1 && (
            <label className="flex items-center gap-2 font-mono text-caption text-ink-muted">
              <span>Org</span>
              <select
                value={activeOrg}
                onChange={(e) => setSelectedOrg(e.target.value)}
                className="rounded-sm border border-rule bg-paper px-2 py-1 text-ink focus:border-ink focus:outline-none"
              >
                {orgs.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>

        {orgs.length === 0 ? (
          <EmptyCard
            title="No organizations yet"
            body="Service tokens belong to an organization and survive member changes — ideal for CI runners and production servers."
            cta={
              <Link
                to="/settings/organizations"
                className="rounded-sm bg-accent-500 px-3 py-1.5 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600"
              >
                Create organization →
              </Link>
            }
          />
        ) : activeOrg ? (
          <ServiceTokensSection orgId={activeOrg} />
        ) : null}
      </section>
    </div>
  )
}

// ─── Shared ────────────────────────────────────────────────────────────────

function EmptyCard({
  title,
  body,
  cta,
}: {
  title: string
  body: string
  cta?: React.ReactNode
}) {
  return (
    <div className="rounded border border-dashed border-rule bg-surface px-5 py-8 text-center">
      <p className="font-sans text-base font-medium text-ink">{title}</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-ink-muted">{body}</p>
      {cta && <div className="mt-4">{cta}</div>}
    </div>
  )
}

function TokenList({
  tokens,
  onRevoke,
  revokingId,
}: {
  tokens: UserTokenItem[]
  onRevoke: (id: string) => void
  revokingId: string | null
}) {
  return (
    <ul className="divide-y divide-dashed divide-rule overflow-hidden rounded border border-rule bg-surface">
      {tokens.map((t) => (
        <li
          key={t.id}
          className="flex items-center justify-between gap-4 px-4 py-3"
        >
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-2">
              <p className="truncate text-sm font-semibold text-ink">
                {tokenLabel(t.label)}
              </p>
              {t.revoked_at && (
                <span className="rounded-sm bg-sunken px-1.5 py-0.5 font-mono text-micro uppercase tracking-wider text-ink-muted">
                  revoked
                </span>
              )}
            </div>
            <p className="mt-0.5 font-mono text-micro text-ink-faint">
              Created {formatDate(t.created_at)}
              {t.last_used_at && (
                <>
                  <span aria-hidden className="mx-1.5">
                    ·
                  </span>
                  <span title={formatDate(t.last_used_at)}>
                    last used {formatRelative(t.last_used_at)}
                  </span>
                </>
              )}
            </p>
          </div>
          {!t.revoked_at && (
            <button
              type="button"
              onClick={() => onRevoke(t.id)}
              disabled={revokingId === t.id}
              className="rounded-sm border border-flag-env/60 px-3 py-1 font-mono text-caption text-flag-env-fg hover:bg-flag-env/10 disabled:opacity-60"
            >
              {revokingId === t.id ? "Revoking…" : "Revoke"}
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}

// ─── Service tokens per-org ────────────────────────────────────────────────

function ServiceTokensSection({ orgId }: { orgId: string }) {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [showRevoked, setShowRevoked] = useState(false)
  const [newLabel, setNewLabel] = useState("")
  const [justCreated, setJustCreated] = useState<string | null>(null)
  const [justCreatedLabel, setJustCreatedLabel] = useState<string>("")

  const { data: tokens = [], isLoading } = useQuery({
    queryKey: SERVICE_TOKENS_KEY(orgId),
    queryFn: () => listServiceTokens(orgId),
  })

  const createMut = useMutation({
    mutationFn: (label: string) => createServiceToken(orgId, label),
    onSuccess: (resp) => {
      toast.success("Service token created")
      setJustCreated(resp.token)
      setJustCreatedLabel(resp.label)
      setNewLabel("")
      setShowForm(false)
      qc.invalidateQueries({ queryKey: SERVICE_TOKENS_KEY(orgId) })
    },
    onError: (e) => toast.error(e.message),
  })

  const revokeMut = useMutation({
    mutationFn: revokeServiceToken,
    onSuccess: () => {
      toast.success("Token revoked")
      qc.invalidateQueries({ queryKey: SERVICE_TOKENS_KEY(orgId) })
    },
    onError: (e) => {
      reportRevokeOutcome(e)
      if (isAlreadyRevokedError(e)) qc.invalidateQueries({ queryKey: SERVICE_TOKENS_KEY(orgId) })
    },
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const label = newLabel.trim()
    if (!label) return
    createMut.mutate(label)
  }

  const visible = showRevoked ? tokens : tokens.filter((t) => !t.revoked_at)
  const hidden = tokens.length - visible.length

  return (
    <div className="space-y-3">
      {justCreated && (
        <JustCreatedBanner
          label={justCreatedLabel}
          token={justCreated}
          onDismiss={() => setJustCreated(null)}
        />
      )}

      <div className="flex items-center justify-between gap-3">
        <p className="font-mono text-micro uppercase tracking-wider text-ink-faint">
          {visible.length} active
          {hidden > 0 && (
            <>
              <span aria-hidden className="mx-1.5">
                ·
              </span>
              <button
                type="button"
                onClick={() => setShowRevoked(true)}
                className="underline decoration-rule underline-offset-2 hover:text-ink"
              >
                {hidden} revoked
              </button>
            </>
          )}
          {showRevoked && hidden >= 0 && (
            <>
              <span aria-hidden className="mx-1.5">
                ·
              </span>
              <button
                type="button"
                onClick={() => setShowRevoked(false)}
                className="underline decoration-rule underline-offset-2 hover:text-ink"
              >
                hide revoked
              </button>
            </>
          )}
        </p>
        {!showForm && (
          <button
            type="button"
            onClick={() => setShowForm(true)}
            className="rounded-sm bg-accent-500 px-3 py-1.5 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600"
          >
            + Create token
          </button>
        )}
      </div>

      {showForm && (
        <form
          onSubmit={submit}
          className="flex flex-wrap items-end gap-2 rounded border border-rule bg-surface p-3"
        >
          <label className="min-w-[200px] flex-1">
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Label
            </span>
            <input
              type="text"
              required
              autoFocus
              value={newLabel}
              onChange={(e) => setNewLabel(e.target.value)}
              placeholder="ci-runner-prod"
              className="mt-1 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            />
          </label>
          <button
            type="submit"
            disabled={!newLabel.trim() || createMut.isPending}
            className="h-[34px] rounded-sm bg-accent-500 px-3 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 disabled:opacity-60"
          >
            {createMut.isPending ? "Creating…" : "Create"}
          </button>
          <button
            type="button"
            onClick={() => {
              setShowForm(false)
              setNewLabel("")
            }}
            className="h-[34px] rounded-sm border border-rule px-3 font-mono text-caption text-ink-muted hover:bg-sunken"
          >
            Cancel
          </button>
        </form>
      )}

      {isLoading ? (
        <p className="px-4 py-3 text-caption text-ink-muted">Loading…</p>
      ) : visible.length === 0 ? (
        <EmptyCard
          title="No service tokens for this org"
          body="Create one above, then paste it into $RECEIPT_TOKEN on your server or CI runner."
        />
      ) : (
        <ul className="divide-y divide-dashed divide-rule overflow-hidden rounded border border-rule bg-surface">
          {visible.map((t) => (
            <ServiceTokenRow
              key={t.id}
              token={t}
              onRevoke={(id) => {
                if (
                  confirm(
                    `Revoke "${tokenLabel(t.label)}"? CI runners using it will stop ingesting.`,
                  )
                ) {
                  revokeMut.mutate(id)
                }
              }}
              revokingId={revokeMut.isPending ? revokeMut.variables : null}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function JustCreatedBanner({
  label,
  token,
  onDismiss,
}: {
  label: string
  token: string
  onDismiss: () => void
}) {
  const [copied, setCopied] = useState(false)
  return (
    <div
      role="status"
      className="rounded border border-flag-migration/40 bg-flag-migration/5 p-4"
    >
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-mono text-micro uppercase tracking-wider text-flag-migration">
          Copy now — shown once
        </p>
        <button
          type="button"
          onClick={onDismiss}
          className="font-mono text-micro uppercase tracking-wider text-ink-faint hover:text-ink"
        >
          dismiss
        </button>
      </div>
      <p className="mt-2 text-sm text-ink">
        <strong>{label}</strong> — this is the only time the token will be
        shown. Store it securely.
      </p>
      <div className="mt-3 flex items-center gap-2">
        <code className="flex-1 overflow-x-auto rounded-sm bg-sunken px-2 py-1.5 font-mono text-caption text-ink">
          {token}
        </code>
        <button
          type="button"
          onClick={async () => {
            await navigator.clipboard.writeText(token)
            setCopied(true)
            toast.success("Copied to clipboard")
            setTimeout(() => setCopied(false), 1500)
          }}
          className="rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink hover:bg-sunken"
        >
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>
    </div>
  )
}

function ServiceTokenRow({
  token,
  onRevoke,
  revokingId,
}: {
  token: ServiceTokenItem
  onRevoke: (id: string) => void
  revokingId: string | null
}) {
  return (
    <li className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <p className="truncate text-sm font-semibold text-ink">
            {tokenLabel(token.label)}
          </p>
          {token.revoked_at && (
            <span className="rounded-sm bg-sunken px-1.5 py-0.5 font-mono text-micro uppercase tracking-wider text-ink-muted">
              revoked
            </span>
          )}
        </div>
        <p className="mt-0.5 font-mono text-micro text-ink-faint">
          Created {formatDate(token.created_at)}
          {token.minted_by_user_id && (
            <>
              <span aria-hidden className="mx-1.5">
                ·
              </span>
              <span>by {token.minted_by_user_id}</span>
            </>
          )}
          {token.last_used_at && (
            <>
              <span aria-hidden className="mx-1.5">
                ·
              </span>
              <span title={formatDate(token.last_used_at)}>
                last used {formatRelative(token.last_used_at)}
              </span>
            </>
          )}
        </p>
      </div>
      {!token.revoked_at && (
        <button
          type="button"
          onClick={() => onRevoke(token.id)}
          disabled={revokingId === token.id}
          className="rounded-sm border border-flag-env/60 px-3 py-1 font-mono text-caption text-flag-env-fg hover:bg-flag-env/10 disabled:opacity-60"
        >
          {revokingId === token.id ? "Revoking…" : "Revoke"}
        </button>
      )}
    </li>
  )
}
