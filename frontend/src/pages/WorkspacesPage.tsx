import { Fragment, useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  apiFetch,
  autoRouteBitbucketRepos,
  autoRouteRepos,
  buildBitbucketAuthUrl,
  buildGithubAuthUrl,
  createWorkspace,
  deleteWorkspace,
  disconnectBitbucket,
  disconnectGithub,
  getBitbucketStatus,
  getGithubStatus,
  listBitbucketRepos,
  listGithubRepos,
  listWorkspaceRepos,
  listWorkspaces,
  patchWorkspace,
  removeWorkspaceRepo,
  type GithubRepo,
  type Workspace,
  type WorkspaceRepo,
} from "../lib/api"
import { extractQuotaLimit, useFeature } from "../lib/features"
import { toast } from "../components/Toaster"

// ─── VCS provider config ────────────────────────────────────────────────────
// GitHub and Bitbucket share one connect / map-repos / auto-route surface; only
// the OAuth provider, host, and status username field differ. A provider config
// captures those differences so <VcsPanel> + <RepoPickerModal> stay generic.

/** Normalized connection state (each provider's raw username field flattened
 *  to `username`), so the panel doesn't branch on provider inside the view. */
interface VcsConnState {
  connected: boolean
  username: string | null
  connected_at?: string | null
  expires_at?: string | null
}

interface VcsProviderConfig {
  slug: "github" | "bitbucket"
  label: string
  /** Auth-callback path this provider redirects back to (matches Root.tsx). */
  callbackPath: string
  statusKey: readonly string[]
  reposKey: readonly string[]
  connectVerb: string
  getStatus: () => Promise<VcsConnState>
  buildAuthUrl: (callback: string) => string
  disconnect: () => Promise<void>
  listRepos: () => Promise<GithubRepo[]>
  autoRoute: (body: {
    workspace_id: string
    repos: string[]
  }) => Promise<{ added: number; skipped_already_mapped: number; errors: number }>
}

const GITHUB_CFG: VcsProviderConfig = {
  slug: "github",
  label: "GitHub",
  callbackPath: "/integrations/github/callback",
  statusKey: ["me", "github"],
  reposKey: ["me", "github", "repos"],
  connectVerb: "Connect GitHub →",
  getStatus: () =>
    getGithubStatus().then((s) => ({
      connected: s.connected,
      username: s.github_login ?? null,
      connected_at: s.connected_at,
      expires_at: s.expires_at,
    })),
  buildAuthUrl: (cb) => buildGithubAuthUrl(cb),
  disconnect: disconnectGithub,
  listRepos: () => listGithubRepos(),
  autoRoute: autoRouteRepos,
}

const BITBUCKET_CFG: VcsProviderConfig = {
  slug: "bitbucket",
  label: "Bitbucket",
  callbackPath: "/integrations/bitbucket/callback",
  statusKey: ["me", "bitbucket"],
  reposKey: ["me", "bitbucket", "repos"],
  connectVerb: "Connect Bitbucket →",
  getStatus: () =>
    getBitbucketStatus().then((s) => ({
      connected: s.connected,
      username: s.bitbucket_username ?? null,
      connected_at: s.connected_at,
      expires_at: s.expires_at,
    })),
  buildAuthUrl: (cb) => buildBitbucketAuthUrl(cb),
  disconnect: disconnectBitbucket,
  listRepos: () => listBitbucketRepos(),
  autoRoute: autoRouteBitbucketRepos,
}

const VCS_PROVIDERS_CFG: VcsProviderConfig[] = [GITHUB_CFG, BITBUCKET_CFG]

interface Organization {
  id: string
  name: string
  slug: string
}

interface OrgListResponse {
  items: Organization[]
}

const WS_KEY = ["me", "workspaces"] as const
const ORGS_KEY = ["me", "organizations"] as const
const reposKey = (wsId: string) =>
  ["me", "workspaces", wsId, "repos"] as const

// ─── Page ───────────────────────────────────────────────────────────────────

export function WorkspacesPage() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data: workspaces = [], isLoading } = useQuery({
    queryKey: WS_KEY,
    queryFn: listWorkspaces,
  })
  const { data: orgsResp } = useQuery({
    queryKey: ORGS_KEY,
    queryFn: () => apiFetch<OrgListResponse>("/me/organizations"),
  })
  const orgs = Array.isArray(orgsResp?.items) ? orgsResp.items : []
  const orgNameById = useMemo(() => {
    const m = new Map<string, string>()
    for (const o of orgs) m.set(o.id, o.name)
    return m
  }, [orgs])

  const { data: workspacesMax } = useFeature("workspaces_max")
  const personalLimit = extractQuotaLimit(workspacesMax)
  const personalCount = workspaces.filter((w) => w.org_id === null).length
  const atPersonalLimit =
    personalLimit !== null && personalLimit >= 0 && personalCount >= personalLimit

  const selected = workspaces.find((w) => w.id === selectedId) ?? workspaces[0] ?? null

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Settings
        </p>
        <h1 className="mt-2 font-sans text-3xl font-semibold text-ink">
          Workspaces
        </h1>
        <p className="mt-2 text-sm text-ink-muted">
          Where your Claude Code sessions land. Connect GitHub or Bitbucket to
          auto-route repos, or create workspaces for specific projects.
        </p>
      </div>

      <div className="space-y-3">
        {VCS_PROVIDERS_CFG.map((cfg) => (
          <VcsPanel key={cfg.slug} config={cfg} workspaces={workspaces} />
        ))}
      </div>

      <div className="grid gap-6 md:grid-cols-[220px_1fr]">
        <WorkspaceSidebar
          workspaces={workspaces}
          orgNameById={orgNameById}
          selectedId={selected?.id ?? null}
          onSelect={setSelectedId}
          loading={isLoading}
          personalLimit={personalLimit}
          personalCount={personalCount}
          atPersonalLimit={atPersonalLimit}
          orgs={orgs}
        />
        {selected ? (
          <WorkspaceDetail
            workspace={selected}
            orgName={selected.org_id ? orgNameById.get(selected.org_id) : null}
            onDeleted={() => {
              setSelectedId(null)
              qc.invalidateQueries({ queryKey: WS_KEY })
            }}
          />
        ) : (
          <p className="text-sm text-ink-muted">
            Create a workspace to get started.
          </p>
        )}
      </div>

      <section className="border-t border-dashed border-rule pt-6">
        <details className="group">
          <summary className="flex cursor-pointer items-center gap-2 font-mono text-caption uppercase tracking-wider text-ink-muted hover:text-ink">
            <span aria-hidden className="transition-transform group-open:rotate-90">
              ›
            </span>
            Advanced routing
          </summary>
          <div className="mt-3 space-y-2 pl-4 text-sm text-ink-muted">
            <p>
              Glob patterns for non-GitHub workflows (self-hosted Git, local
              paths). Most users don't need this — map your repos above instead.
            </p>
            <Link
              to="/settings/routing"
              className="inline-block underline decoration-rule underline-offset-2 hover:text-ink"
            >
              Open advanced routing →
            </Link>
          </div>
        </details>
      </section>
    </div>
  )
}

// ─── VCS provider panel (GitHub / Bitbucket) ─────────────────────────────────

function VcsPanel({
  config,
  workspaces,
}: {
  config: VcsProviderConfig
  workspaces: Workspace[]
}) {
  const qc = useQueryClient()
  const [pickerOpen, setPickerOpen] = useState(false)

  const { data: status } = useQuery<VcsConnState>({
    queryKey: config.statusKey,
    queryFn: config.getStatus,
    meta: { silent: true },
  })

  const disconnectMut = useMutation({
    mutationFn: config.disconnect,
    onSuccess: () => {
      toast.success(`${config.label} disconnected`)
      qc.invalidateQueries({ queryKey: config.statusKey })
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  const handleConnect = () => {
    const callback = `${window.location.origin}${config.callbackPath}`
    try {
      window.location.href = config.buildAuthUrl(callback)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="rounded border border-rule bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-3">
            <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
              {config.label}
            </h2>
            {status?.connected && (
              <span className="font-mono text-micro text-ink-faint">
                @{status.username ?? "?"}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-ink-muted">
            {!status
              ? "Checking status…"
              : status.connected
                ? "Pick repos and assign each to a workspace — sessions auto-route."
                : "Connect once, then map any repo to any workspace with one click."}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {status?.connected ? (
            <>
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="rounded-sm bg-accent-500 px-3 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600"
              >
                Map repos
              </button>
              <button
                type="button"
                onClick={() => disconnectMut.mutate()}
                disabled={disconnectMut.isPending}
                className="rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink-muted hover:bg-sunken hover:text-ink disabled:opacity-60"
              >
                Disconnect
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={handleConnect}
              className="rounded-sm bg-accent-500 px-3 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600"
            >
              {config.connectVerb}
            </button>
          )}
        </div>
      </div>

      {pickerOpen && (
        <RepoPickerModal
          config={config}
          workspaces={workspaces}
          onClose={() => setPickerOpen(false)}
        />
      )}
    </div>
  )
}

// ─── Workspace sidebar (master) ────────────────────────────────────────────

function WorkspaceSidebar({
  workspaces,
  orgNameById,
  selectedId,
  onSelect,
  loading,
  personalLimit,
  personalCount,
  atPersonalLimit,
  orgs,
}: {
  workspaces: Workspace[]
  orgNameById: Map<string, string>
  selectedId: string | null
  onSelect: (id: string) => void
  loading: boolean
  personalLimit: number | null
  personalCount: number
  atPersonalLimit: boolean
  orgs: Organization[]
}) {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState("")
  const [targetOrgId, setTargetOrgId] = useState<string>("")

  const createMut = useMutation({
    mutationFn: (payload: { name: string; org_id: string | null }) =>
      createWorkspace(payload),
    onSuccess: (w) => {
      toast.success("Workspace created")
      setName("")
      setTargetOrgId("")
      setShowForm(false)
      qc.invalidateQueries({ queryKey: WS_KEY })
      onSelect(w.id)
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const v = name.trim()
    if (!v) return
    createMut.mutate({ name: v, org_id: targetOrgId || null })
  }

  // Group workspaces by scope (personal / org) for the sidebar.
  const personal = workspaces.filter((w) => w.org_id === null)
  const byOrg = new Map<string, Workspace[]>()
  for (const w of workspaces) {
    if (!w.org_id) continue
    const arr = byOrg.get(w.org_id) ?? []
    arr.push(w)
    byOrg.set(w.org_id, arr)
  }

  return (
    <aside className="space-y-2">
      <ul className="divide-y divide-dashed divide-rule overflow-hidden rounded border border-rule bg-surface">
        {loading ? (
          <li className="px-3 py-2 text-caption text-ink-muted">Loading…</li>
        ) : workspaces.length === 0 ? (
          <li className="px-3 py-2 text-caption text-ink-muted">
            No workspaces yet
          </li>
        ) : (
          <>
            {personal.length > 0 && (
              <li className="bg-sunken/40 px-3 py-1">
                <span className="font-mono text-micro uppercase tracking-wider text-ink-faint">
                  Personal
                  {personalLimit !== null && personalLimit >= 0 && (
                    <span className="ml-2 tabular-nums">
                      {personalCount} / {personalLimit === -1 ? "∞" : personalLimit}
                    </span>
                  )}
                </span>
              </li>
            )}
            {personal.map((w) => (
              <WorkspaceRowButton
                key={w.id}
                workspace={w}
                selected={w.id === selectedId}
                onClick={() => onSelect(w.id)}
                subtitle={w.slug}
              />
            ))}
            {[...byOrg.entries()].map(([orgId, ws]) => (
              <Fragment key={orgId}>
                <li className="bg-sunken/40 px-3 py-1">
                  <span className="font-mono text-micro uppercase tracking-wider text-ink-faint">
                    {orgNameById.get(orgId) ?? "Organization"}
                  </span>
                </li>
                {ws.map((w) => (
                  <WorkspaceRowButton
                    key={w.id}
                    workspace={w}
                    selected={w.id === selectedId}
                    onClick={() => onSelect(w.id)}
                    subtitle={w.slug}
                  />
                ))}
              </Fragment>
            ))}
          </>
        )}
      </ul>

      {!showForm ? (
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="w-full rounded-sm border border-dashed border-rule px-3 py-2 font-mono text-caption text-ink-muted hover:border-ink hover:text-ink"
        >
          + New workspace
        </button>
      ) : (
        <form
          onSubmit={submit}
          className="space-y-2 rounded-sm border border-rule bg-surface p-3"
        >
          <label className="block">
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Name
            </span>
            <input
              type="text"
              required
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Side projects"
              className="mt-1 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            />
          </label>
          <label className="block">
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Scope
            </span>
            <select
              value={targetOrgId}
              onChange={(e) => setTargetOrgId(e.target.value)}
              className="mt-1 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            >
              <option value="" disabled={atPersonalLimit}>
                Personal
                {atPersonalLimit ? " (limit reached)" : ""}
              </option>
              {orgs.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>
          </label>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={
                createMut.isPending ||
                !name.trim() ||
                (targetOrgId === "" && atPersonalLimit)
              }
              className="flex-1 rounded-sm bg-accent-500 px-3 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 disabled:opacity-60"
            >
              {createMut.isPending ? "Creating…" : "Create"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink-muted hover:bg-sunken"
            >
              Cancel
            </button>
          </div>
          {atPersonalLimit && targetOrgId === "" && (
            <p className="text-caption text-ink-muted">
              Personal workspaces maxed out.{" "}
              <Link
                to="/settings/billing"
                className="underline decoration-rule underline-offset-2 hover:text-ink"
              >
                Upgrade →
              </Link>
            </p>
          )}
        </form>
      )}
    </aside>
  )
}

function WorkspaceRowButton({
  workspace,
  selected,
  onClick,
  subtitle,
}: {
  workspace: Workspace
  selected: boolean
  onClick: () => void
  subtitle: string
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={
          "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-sunken " +
          (selected ? "bg-sunken" : "")
        }
      >
        <span className="truncate text-sm font-semibold text-ink">
          {workspace.name}
        </span>
        <span className="truncate font-mono text-micro text-ink-faint">
          {subtitle}
        </span>
      </button>
    </li>
  )
}

// ─── Workspace detail (right pane) ─────────────────────────────────────────

function WorkspaceDetail({
  workspace,
  orgName,
  onDeleted,
}: {
  workspace: Workspace
  orgName: string | null | undefined
  onDeleted: () => void
}) {
  const qc = useQueryClient()
  const [rename, setRename] = useState(workspace.name)

  // Reset rename field when switching workspace.
  useMemo(() => {
    setRename(workspace.name)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id])

  const renameMut = useMutation({
    mutationFn: () => patchWorkspace(workspace.id, { name: rename.trim() }),
    onSuccess: () => {
      toast.success("Renamed")
      qc.invalidateQueries({ queryKey: WS_KEY })
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  const deleteMut = useMutation({
    mutationFn: () => deleteWorkspace(workspace.id),
    onSuccess: onDeleted,
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  const scopeLabel = workspace.org_id ? orgName ?? "Organization" : "Personal"

  return (
    <section className="space-y-6">
      {/* Rename + delete */}
      <div className="rounded border border-rule bg-surface p-4">
        <div className="flex items-end gap-3">
          <label className="flex-1">
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Name
            </span>
            <input
              type="text"
              value={rename}
              onChange={(e) => setRename(e.target.value)}
              className="mt-1 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            />
          </label>
          <button
            type="button"
            onClick={() => renameMut.mutate()}
            disabled={
              renameMut.isPending ||
              !rename.trim() ||
              rename.trim() === workspace.name
            }
            className="h-[34px] rounded-sm border border-rule px-3 font-mono text-caption text-ink hover:bg-sunken disabled:opacity-60"
          >
            {renameMut.isPending ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => {
              if (
                confirm(
                  `Delete "${workspace.name}"? Sessions keep their history.`,
                )
              ) {
                deleteMut.mutate()
              }
            }}
            disabled={deleteMut.isPending}
            className="h-[34px] rounded-sm border border-flag-env/60 px-3 font-mono text-caption text-flag-env-fg hover:bg-flag-env/10 disabled:opacity-60"
          >
            Delete
          </button>
        </div>
        <p className="mt-2 font-mono text-micro text-ink-faint">
          scope: {scopeLabel} · slug: {workspace.slug} · id:{" "}
          {workspace.id.slice(0, 8)}…
        </p>
      </div>

      {/* View sessions shortcut */}
      <div className="flex items-center justify-between gap-3 rounded border border-rule bg-surface px-4 py-2">
        <div>
          <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
            Sessions
          </p>
          <p className="text-sm text-ink-muted">
            See every Claude Code session that landed in this workspace.
          </p>
        </div>
        <Link
          to={`/?workspace_id=${workspace.id}`}
          className="rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink hover:bg-sunken"
        >
          View →
        </Link>
      </div>

      {/* Mapped repos */}
      <ReposSection workspace={workspace} />
    </section>
  )
}

// ─── Mapped repos section ──────────────────────────────────────────────────

function ReposSection({ workspace }: { workspace: Workspace }) {
  const qc = useQueryClient()

  const { data: repos = [], isLoading } = useQuery<WorkspaceRepo[]>({
    queryKey: reposKey(workspace.id),
    queryFn: () => listWorkspaceRepos(workspace.id),
    meta: { silent: true },
  })

  const unmapMut = useMutation({
    mutationFn: (repoId: string) => removeWorkspaceRepo(workspace.id, repoId),
    onSuccess: () => {
      toast.success("Repo unmapped")
      qc.invalidateQueries({ queryKey: reposKey(workspace.id) })
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  return (
    <div className="rounded border border-rule bg-surface">
      <header className="flex items-center justify-between border-b border-rule px-4 py-2">
        <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Mapped repos · {repos.length}
        </h2>
      </header>
      <ul className="divide-y divide-dashed divide-rule">
        {isLoading ? (
          <li className="px-4 py-3 text-caption text-ink-muted">Loading…</li>
        ) : repos.length === 0 ? (
          <li className="px-4 py-4 text-caption text-ink-muted">
            No repos mapped yet. Use "Map repos" above to assign GitHub or
            Bitbucket repos to this workspace — sessions from those repos will
            auto-route here.
          </li>
        ) : (
          repos.map((r) => (
            <li
              key={r.id}
              className="flex items-center justify-between gap-3 px-4 py-2"
            >
              <code className="truncate font-mono text-caption text-ink">
                {r.host !== "github.com" && (
                  <span className="text-ink-faint">{r.host}/</span>
                )}
                {r.full_name}
              </code>
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Unmap ${r.full_name}?`)) {
                    unmapMut.mutate(r.id)
                  }
                }}
                className="rounded-sm border border-rule px-2 py-1 font-mono text-micro text-ink-faint hover:bg-sunken hover:text-ink"
              >
                Unmap
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  )
}

// ─── Repo picker modal ─────────────────────────────────────────────────────

function RepoPickerModal({
  config,
  workspaces,
  onClose,
}: {
  config: VcsProviderConfig
  workspaces: Workspace[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [workspaceId, setWorkspaceId] = useState<string>(
    workspaces[0]?.id ?? "",
  )
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [search, setSearch] = useState("")

  const { data: repos = [], isLoading, refetch } = useQuery<GithubRepo[]>({
    queryKey: config.reposKey,
    queryFn: config.listRepos,
  })

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return repos
    return repos.filter((r) => r.full_name.toLowerCase().includes(q))
  }, [repos, search])

  const groupedByOwner = useMemo(() => {
    const m = new Map<string, GithubRepo[]>()
    for (const r of filtered) {
      const arr = m.get(r.owner) ?? []
      arr.push(r)
      m.set(r.owner, arr)
    }
    return Array.from(m.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [filtered])

  const applyMut = useMutation({
    mutationFn: () =>
      config.autoRoute({
        workspace_id: workspaceId,
        repos: Array.from(selected),
      }),
    onSuccess: (resp) => {
      toast.success(
        `Added ${resp.added} repo(s)`,
        resp.skipped_already_mapped
          ? `${resp.skipped_already_mapped} already mapped elsewhere — skipped.`
          : undefined,
      )
      qc.invalidateQueries({ queryKey: config.reposKey })
      qc.invalidateQueries({ queryKey: WS_KEY })
      onClose()
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  })

  const toggleRepo = (fullName: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(fullName)) next.delete(fullName)
      else next.add(fullName)
      return next
    })
  }

  const toggleOwner = (repos: GithubRepo[]) => {
    const all = repos.filter(
      (r) => !r.mapped_workspace_id || r.mapped_workspace_id === workspaceId,
    )
    setSelected((prev) => {
      const next = new Set(prev)
      const allSelected = all.every((r) => next.has(r.full_name))
      for (const r of all) {
        if (allSelected) next.delete(r.full_name)
        else next.add(r.full_name)
      }
      return next
    })
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="repo-picker-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 px-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded border border-rule bg-surface shadow-lg">
        <header className="flex items-start justify-between gap-4 border-b border-rule p-4">
          <div>
            <p className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              {config.label}
            </p>
            <h3
              id="repo-picker-title"
              className="mt-1 font-sans text-lg font-semibold text-ink"
            >
              Map repos to a workspace
            </h3>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-sm border border-rule px-2 py-1 font-mono text-caption text-ink-muted hover:bg-sunken"
          >
            ×
          </button>
        </header>

        <div className="grid gap-3 border-b border-rule p-4 sm:grid-cols-[2fr_1fr]">
          <label className="flex flex-col gap-1">
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Target workspace
            </span>
            <select
              value={workspaceId}
              onChange={(e) => setWorkspaceId(e.target.value)}
              className="h-9 rounded-sm border border-rule bg-paper px-2 font-sans text-sm text-ink focus:border-ink focus:outline-none"
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                  {w.org_id ? " · org" : " · personal"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Filter
            </span>
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="owner/repo"
              className="h-9 rounded-sm border border-rule bg-paper px-2 font-sans text-sm text-ink focus:border-ink focus:outline-none"
            />
          </label>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div role="status" className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <div
                  key={i}
                  aria-hidden
                  className="h-8 rounded-sm bg-sunken/60 motion-safe:animate-pulse"
                />
              ))}
            </div>
          ) : filtered.length === 0 ? (
            <p className="py-8 text-center text-sm text-ink-muted">
              {repos.length === 0
                ? `No repos found. Did ${config.label} grant repo scope?`
                : "No repos match your filter."}
            </p>
          ) : (
            <div className="space-y-5">
              {groupedByOwner.map(([owner, ownerRepos]) => {
                const selectable = ownerRepos.filter(
                  (r) =>
                    !r.mapped_workspace_id ||
                    r.mapped_workspace_id === workspaceId,
                )
                const allSelected =
                  selectable.length > 0 &&
                  selectable.every((r) => selected.has(r.full_name))
                return (
                  <div key={owner}>
                    <div className="mb-2 flex items-baseline justify-between gap-2 border-b border-dashed border-rule pb-1">
                      <h4 className="font-mono text-caption text-ink">
                        <span className="text-ink-faint">@</span>
                        {owner}
                        <span className="ml-2 tabular-nums text-ink-faint">
                          ({ownerRepos.length})
                        </span>
                      </h4>
                      {selectable.length > 0 && (
                        <button
                          type="button"
                          onClick={() => toggleOwner(ownerRepos)}
                          className="font-mono text-micro uppercase tracking-wider text-ink-muted hover:text-ink"
                        >
                          {allSelected ? "deselect all" : "select all"}
                        </button>
                      )}
                    </div>
                    <ul role="list" className="divide-y divide-rule">
                      {ownerRepos.map((r) => {
                        const mappedHere =
                          r.mapped_workspace_id === workspaceId
                        const mappedElsewhere =
                          r.mapped_workspace_id &&
                          r.mapped_workspace_id !== workspaceId
                        const checked = selected.has(r.full_name)
                        const disabled =
                          mappedHere || Boolean(mappedElsewhere)
                        return (
                          <li key={r.id}>
                            <label
                              htmlFor={`repo-${r.id}`}
                              className={
                                "flex items-center gap-3 py-2 " +
                                (disabled
                                  ? "cursor-not-allowed opacity-60"
                                  : "cursor-pointer hover:bg-sunken/40")
                              }
                            >
                              <input
                                type="checkbox"
                                id={`repo-${r.id}`}
                                checked={checked}
                                onChange={() => toggleRepo(r.full_name)}
                                disabled={disabled}
                                className="h-4 w-4 accent-ink"
                              />
                              <code className="truncate font-mono text-sm text-ink">
                                {r.repo}
                              </code>
                              <span className="ml-auto flex shrink-0 items-center gap-2">
                                {r.private && (
                                  <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
                                    private
                                  </span>
                                )}
                                {mappedHere && (
                                  <span className="font-mono text-micro uppercase tracking-wider text-flag-none">
                                    mapped here
                                  </span>
                                )}
                                {mappedElsewhere && (
                                  <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
                                    elsewhere
                                  </span>
                                )}
                              </span>
                            </label>
                          </li>
                        )
                      })}
                    </ul>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <footer className="flex items-center justify-between gap-4 border-t border-rule p-4">
          <span className="font-mono text-caption tabular-nums text-ink-muted">
            {selected.size} selected
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => refetch()}
              className="rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink hover:bg-sunken"
            >
              Refresh
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink hover:bg-sunken"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => applyMut.mutate()}
              disabled={
                selected.size === 0 || !workspaceId || applyMut.isPending
              }
              className="rounded-sm bg-accent-500 px-3 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 disabled:opacity-60"
            >
              {applyMut.isPending
                ? "Mapping…"
                : `Map ${selected.size || ""} →`.trim()}
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}

