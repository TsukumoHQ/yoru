import { useMemo, useState } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { apiFetch, ApiError, downloadOrgAuditExport } from "../lib/api"
import { useInstanceConfig } from "../lib/config"
import { toast } from "../components/Toaster"

// ─── Types ──────────────────────────────────────────────────────────────────

type OrgType = "personal" | "team"
type MemberRole = "owner" | "admin" | "member"

interface Organization {
  id: string
  name: string
  slug: string
  type: OrgType
  owner_id: string
  avatar_url?: string | null
  created_at: string
  updated_at: string
  deleted_at?: string | null
}

interface OrgListResponse {
  items: Organization[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

interface Member {
  user_id: string
  org_id: string
  role: MemberRole
  joined_at: string
  user_email?: string
  user_name?: string
  invited_by?: string | null
}

interface Invitation {
  id: string
  org_id: string
  email: string
  role: "admin" | "member"
  token: string
  invited_by: string
  expires_at: string
  accepted_at?: string | null
  created_at: string
}

interface SubscriptionFeatures {
  features?: Record<string, unknown>
  plan_name?: string
}

function extractSeatsMax(sub: SubscriptionFeatures | null | undefined): number {
  if (!sub?.features) return 1  // no sub fetched → assume Free
  const raw = sub.features["seats_max"]
  if (typeof raw === "number") return raw
  if (raw && typeof raw === "object" && "limit" in raw) {
    const lim = (raw as { limit: unknown }).limit
    if (typeof lim === "number") return lim
  }
  return 1
}

// ─── Data ───────────────────────────────────────────────────────────────────

const ORGS_KEY = ["me", "organizations"] as const
const orgKey = (id: string) => ["me", "organizations", id] as const
const membersKey = (id: string) => ["me", "organizations", id, "members"] as const
const invitesKey = (id: string) => ["me", "organizations", id, "invitations"] as const

async function listOrgs(): Promise<Organization[]> {
  try {
    const r = await apiFetch<OrgListResponse | Organization[]>("/me/organizations")
    return Array.isArray(r) ? r : (Array.isArray(r?.items) ? r.items : [])
  } catch {
    return []
  }
}

async function createOrg(payload: { name: string }): Promise<Organization> {
  // Post-Phase W: only `type='team'` orgs are user-createable.
  // Personal workspaces live in the workspaces table, not here.
  return apiFetch<Organization>("/me/organizations", {
    method: "POST",
    body: JSON.stringify({ ...payload, type: "team" }),
  })
}

async function patchOrg(id: string, patch: Partial<Pick<Organization, "name" | "avatar_url">>): Promise<Organization> {
  return apiFetch<Organization>(`/me/organizations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  })
}

async function deleteOrg(id: string): Promise<void> {
  await apiFetch<void>(`/me/organizations/${id}`, { method: "DELETE" })
}

async function listMembers(orgId: string): Promise<Member[]> {
  try {
    const r = await apiFetch<{ items: Member[] } | Member[]>(`/me/organizations/${orgId}/members`)
    return Array.isArray(r) ? r : (r?.items ?? [])
  } catch {
    return []
  }
}

async function removeMember(orgId: string, userId: string): Promise<void> {
  await apiFetch<void>(`/me/organizations/${orgId}/members/${userId}`, { method: "DELETE" })
}

async function listInvitations(orgId: string): Promise<Invitation[]> {
  try {
    const r = await apiFetch<{ items: Invitation[] } | Invitation[]>(
      `/me/organizations/${orgId}/invitations`,
    )
    return Array.isArray(r) ? r : (r?.items ?? [])
  } catch {
    return []
  }
}

async function createInvitation(
  orgId: string,
  payload: { email: string; role: "admin" | "member" },
): Promise<Invitation> {
  return apiFetch<Invitation>(`/me/organizations/${orgId}/invitations`, {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

async function cancelInvitation(orgId: string, invitationId: string): Promise<void> {
  await apiFetch<void>(`/me/organizations/${orgId}/invitations/${invitationId}`, {
    method: "DELETE",
  })
}

// ─── Page ───────────────────────────────────────────────────────────────────

export function OrganizationsPage() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data: orgs = [], isLoading } = useQuery({
    queryKey: ORGS_KEY,
    queryFn: listOrgs,
  })

  const { data: subscription } = useQuery<SubscriptionFeatures | null>({
    queryKey: ["me", "subscription"],
    queryFn: async () => {
      try {
        return await apiFetch<SubscriptionFeatures>("/me/subscription")
      } catch {
        return null
      }
    },
    staleTime: 60_000,
    meta: { silent: true },
  })
  const config = useInstanceConfig()
  const seatsMax = extractSeatsMax(subscription)
  const planName = subscription?.plan_name ?? "Free"
  // Seats are a billing concept. On a self-hosted instance (billing disabled)
  // there is no seat limit and no upgrade path — a studio hosting N client-orgs
  // must always be able to invite/manage members. Only gate on seats when
  // billing is actually enabled.
  const canInvite = !config.billing_enabled || seatsMax < 0 || seatsMax > 1

  const created = useMutation({
    mutationFn: createOrg,
    onSuccess: (org) => {
      qc.invalidateQueries({ queryKey: ORGS_KEY })
      qc.invalidateQueries({ queryKey: ["me", "workspaces"] })
      setSelectedId(org.id)
      toast.success(
        "Organization created",
        "A default workspace was created inside. Map your repos next.",
      )
    },
    onError: (err) => {
      toast.error("Couldn't create org", err instanceof Error ? err.message : String(err))
    },
  })

  const selected = orgs.find((o) => o.id === selectedId) ?? orgs[0] ?? null

  const isEmpty = !isLoading && orgs.length === 0

  return (
    <div className="mx-auto max-w-5xl space-y-8">
      <div>
        <p className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Settings
        </p>
        <h1 className="mt-2 font-sans text-3xl font-semibold text-ink">
          Organizations
        </h1>
        <p className="mt-2 text-sm text-ink-muted">
          Shared containers for teammates. Create one to invite collaborators,
          then add workspaces inside for each project or team.
        </p>
      </div>

      {isEmpty ? (
        <EmptyOrgState
          onCreate={(name) => created.mutate({ name })}
          creating={created.isPending}
        />
      ) : (
        <div className="grid gap-6 md:grid-cols-[220px_1fr]">
          <OrgList
            orgs={orgs}
            selectedId={selected?.id ?? null}
            onSelect={setSelectedId}
            onCreate={(name) => created.mutate({ name })}
            loading={isLoading}
            creating={created.isPending}
          />
          {selected ? (
            <div className="space-y-4">
              {!canInvite && (
                <div className="flex flex-wrap items-center gap-3 rounded border border-flag-migration/40 bg-flag-migration/5 px-4 py-3 text-sm text-ink">
                  <span className="font-mono text-micro font-semibold uppercase tracking-wider text-flag-migration">
                    {planName}
                  </span>
                  <p className="flex-1">
                    You're alone on this org. Upgrade to invite teammates and
                    unlock shared workspaces.
                  </p>
                  <Link
                    to="/settings/billing"
                    className="rounded-sm border border-flag-migration/40 px-3 py-1 font-mono text-caption text-ink hover:bg-flag-migration/10"
                  >
                    See plans →
                  </Link>
                </div>
              )}
              <OrgDetail
                org={selected}
                canInvite={canInvite}
                onDeleted={() => {
                  setSelectedId(null)
                  qc.invalidateQueries({ queryKey: ORGS_KEY })
                }}
              />
            </div>
          ) : (
            <p className="text-sm text-ink-muted">
              Pick an organization from the list to see its settings.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Empty state ────────────────────────────────────────────────────────────

function EmptyOrgState({
  onCreate,
  creating,
}: {
  onCreate: (name: string) => void
  creating: boolean
}) {
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState("")
  return (
    <div className="rounded border border-dashed border-rule bg-surface px-6 py-12 text-center">
      <p className="font-sans text-base font-medium text-ink">
        No organizations yet
      </p>
      <p className="mx-auto mt-2 max-w-md text-sm text-ink-muted">
        Organizations let you share workspaces with teammates. Each org has
        its own seats, billing, and set of workspaces.
      </p>
      {!showForm ? (
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="mt-4 rounded-sm bg-accent-500 px-4 py-1.5 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600"
        >
          + Create organization
        </button>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (!name.trim()) return
            onCreate(name.trim())
          }}
          className="mx-auto mt-4 flex max-w-md flex-wrap items-end gap-2"
        >
          <label className="flex-1">
            <span className="sr-only">Organization name</span>
            <input
              type="text"
              required
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Acme Inc."
              className="h-9 w-full rounded-sm border border-rule bg-paper px-2 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            />
          </label>
          <button
            type="submit"
            disabled={creating || !name.trim()}
            className="h-9 rounded-sm bg-accent-500 px-3 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 disabled:opacity-60"
          >
            {creating ? "Creating…" : "Create"}
          </button>
          <button
            type="button"
            onClick={() => setShowForm(false)}
            className="h-9 rounded-sm border border-rule px-3 font-mono text-caption text-ink-muted hover:bg-sunken"
          >
            Cancel
          </button>
        </form>
      )}
    </div>
  )
}

// ─── Org list sidebar ──────────────────────────────────────────────────────

function OrgList({
  orgs,
  selectedId,
  onSelect,
  onCreate,
  loading,
  creating,
}: {
  orgs: Organization[]
  selectedId: string | null
  onSelect: (id: string) => void
  onCreate: (name: string) => void
  loading: boolean
  creating: boolean
}) {
  const [showForm, setShowForm] = useState(false)
  const [name, setName] = useState("")

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    onCreate(name.trim())
    setName("")
    setShowForm(false)
  }

  return (
    <aside className="space-y-2">
      <ul className="divide-y divide-dashed divide-rule overflow-hidden rounded border border-rule bg-surface">
        {loading ? (
          <li className="px-3 py-2 text-caption text-ink-muted">Loading…</li>
        ) : orgs.length === 0 ? (
          <li className="px-3 py-2 text-caption text-ink-muted">No orgs yet</li>
        ) : (
          orgs.map((o) => (
            <li key={o.id}>
              <button
                type="button"
                onClick={() => onSelect(o.id)}
                className={
                  "flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-sunken " +
                  (o.id === selectedId ? "bg-sunken" : "")
                }
              >
                <span className="truncate text-sm font-semibold text-ink">
                  {o.name}
                </span>
                <span className="truncate font-mono text-micro text-ink-faint">
                  {o.slug}
                </span>
              </button>
            </li>
          ))
        )}
      </ul>
      {!showForm ? (
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="w-full rounded-sm border border-dashed border-rule px-3 py-2 font-mono text-caption text-ink-muted hover:border-ink hover:text-ink"
        >
          + New organization
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
              placeholder="Acme Inc."
              className="mt-1 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            />
          </label>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={creating}
              className="flex-1 rounded-sm bg-accent-500 px-3 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 disabled:opacity-60"
            >
              {creating ? "Creating…" : "Create"}
            </button>
            <button
              type="button"
              onClick={() => setShowForm(false)}
              className="rounded-sm border border-rule px-3 py-1 font-mono text-caption text-ink-muted hover:bg-sunken"
            >
              Cancel
            </button>
          </div>
        </form>
      )}
    </aside>
  )
}

// ─── Org detail ────────────────────────────────────────────────────────────

function OrgDetail({ org, canInvite, onDeleted }: { org: Organization; canInvite: boolean; onDeleted: () => void }) {
  const qc = useQueryClient()
  const [exporting, setExporting] = useState(false)

  async function onExport() {
    if (exporting) return
    setExporting(true)
    try {
      const { truncated } = await downloadOrgAuditExport(org.id)
      toast.success(
        "Audit bundle exported",
        truncated
          ? "Capped at 10,000 sessions — narrow the date range for the rest."
          : "Signed EU AI Act bundle downloaded.",
      )
    } catch (err) {
      toast.error("Export failed", err instanceof Error ? err.message : String(err))
    } finally {
      setExporting(false)
    }
  }

  const { data: members = [] } = useQuery({
    queryKey: membersKey(org.id),
    queryFn: () => listMembers(org.id),
  })

  const { data: invitations = [] } = useQuery({
    queryKey: invitesKey(org.id),
    queryFn: () => listInvitations(org.id),
  })

  const renameM = useMutation({
    mutationFn: (name: string) => patchOrg(org.id, { name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ORGS_KEY })
      qc.invalidateQueries({ queryKey: orgKey(org.id) })
      toast.success("Renamed")
    },
    onError: (err) => toast.error("Rename failed", err instanceof Error ? err.message : String(err)),
  })

  const delM = useMutation({
    mutationFn: () => deleteOrg(org.id),
    onSuccess: onDeleted,
    onError: (err) => toast.error("Delete failed", err instanceof Error ? err.message : String(err)),
  })

  const pendingInvitations = invitations.filter((i) => !i.accepted_at)

  return (
    <section className="space-y-6">
      {/* Header — inline-editable name + destructive action */}
      <div className="rounded border border-rule bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <InlineNameEdit
              value={org.name}
              disabled={renameM.isPending}
              onSave={(next) => {
                if (next === org.name) return
                renameM.mutate(next)
              }}
            />
            <p className="mt-1 font-mono text-micro text-ink-faint">
              slug: {org.slug} · id: {org.id.slice(0, 8)}…
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => { void onExport() }}
              disabled={exporting}
              title="Download a signed EU AI Act audit bundle for this organization"
              className="rounded-sm border border-rule px-3 py-1.5 font-mono text-caption text-ink hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 disabled:opacity-60"
            >
              {exporting ? "Exporting…" : "Export audit bundle"}
            </button>
            <button
              type="button"
              onClick={() => {
                if (
                  confirm(
                    `Delete "${org.name}"? This soft-deletes (30-day recovery).`,
                  )
                ) {
                  delM.mutate()
                }
              }}
              disabled={delM.isPending}
              className="rounded-sm border border-flag-env/60 px-3 py-1.5 font-mono text-caption text-flag-env-fg hover:bg-flag-env/10 disabled:opacity-60"
            >
              Delete org
            </button>
          </div>
        </div>
      </div>

      {/* Members */}
      <MembersSection orgId={org.id} members={members} />

      {/* Invitations */}
      <InvitationsSection
        orgId={org.id}
        invitations={pendingInvitations}
        canInvite={canInvite}
      />
    </section>
  )
}

function InlineNameEdit({
  value,
  onSave,
  disabled,
}: {
  value: string
  onSave: (next: string) => void
  disabled?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)

  // Re-sync when parent value changes (e.g. switching org).
  useMemo(() => {
    setDraft(value)
    setEditing(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  if (!editing) {
    return (
      <div className="flex items-center gap-2">
        <h2 className="truncate font-sans text-lg font-semibold text-ink">
          {value}
        </h2>
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="font-mono text-micro uppercase tracking-wider text-ink-faint hover:text-ink"
          title="Rename"
        >
          rename
        </button>
      </div>
    )
  }

  const commit = () => {
    const trimmed = draft.trim()
    if (trimmed && trimmed !== value) {
      onSave(trimmed)
    }
    setEditing(false)
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        commit()
      }}
      className="flex items-center gap-2"
    >
      <input
        type="text"
        value={draft}
        autoFocus
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setDraft(value)
            setEditing(false)
          }
        }}
        disabled={disabled}
        className="w-full max-w-sm rounded-sm border border-rule bg-paper px-2 py-1 font-sans text-lg font-semibold text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500 disabled:opacity-60"
      />
      <button
        type="submit"
        disabled={disabled || !draft.trim() || draft.trim() === value}
        className="rounded-sm bg-accent-500 px-2 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 disabled:opacity-60"
      >
        Save
      </button>
    </form>
  )
}

// ─── Members ───────────────────────────────────────────────────────────────

function MembersSection({ orgId, members }: { orgId: string; members: Member[] }) {
  const qc = useQueryClient()

  const removeM = useMutation({
    mutationFn: (userId: string) => removeMember(orgId, userId),
    onSuccess: () => qc.invalidateQueries({ queryKey: membersKey(orgId) }),
    onError: (err) => toast.error("Remove failed", err instanceof Error ? err.message : String(err)),
  })

  return (
    <div className="rounded border border-rule bg-surface">
      <header className="flex items-center justify-between border-b border-rule px-4 py-2">
        <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Members · {members.length}
        </h2>
      </header>
      <ul className="divide-y divide-dashed divide-rule">
        {members.length === 0 ? (
          <li className="px-4 py-3 text-caption text-ink-muted">No members.</li>
        ) : (
          members.map((m) => (
            <li key={m.user_id} className="flex items-center justify-between gap-3 px-4 py-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-ink">{m.user_email ?? m.user_name ?? m.user_id}</p>
                <p className="font-mono text-micro text-ink-faint">joined {m.joined_at.slice(0, 10)}</p>
              </div>
              <RoleBadge role={m.role} />
              {m.role !== "owner" && (
                <button
                  type="button"
                  onClick={() => removeM.mutate(m.user_id)}
                  className="rounded-sm border border-rule px-2 py-1 font-mono text-micro text-ink-faint hover:bg-sunken hover:text-ink"
                >
                  Remove
                </button>
              )}
            </li>
          ))
        )}
      </ul>
    </div>
  )
}

function RoleBadge({ role }: { role: MemberRole }) {
  const style: Record<MemberRole, string> = {
    owner:  "bg-ink text-paper",
    admin:  "bg-accent-500/10 text-accent-500 ring-1 ring-inset ring-accent-500/40",
    member: "bg-sunken text-ink-muted ring-1 ring-inset ring-rule",
  }
  return (
    <span
      className={
        "rounded-sm px-2 py-0.5 font-mono text-micro font-semibold uppercase tracking-wider " + style[role]
      }
    >
      {role}
    </span>
  )
}

// ─── Invitations ───────────────────────────────────────────────────────────

function InvitationsSection({
  orgId,
  invitations,
  canInvite,
}: {
  orgId: string
  invitations: Invitation[]
  canInvite: boolean
}) {
  const qc = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [email, setEmail] = useState("")
  const [role, setRole] = useState<"admin" | "member">("member")

  const invite = useMutation({
    mutationFn: () => createInvitation(orgId, { email: email.trim(), role }),
    onSuccess: () => {
      setEmail("")
      setShowForm(false)
      qc.invalidateQueries({ queryKey: invitesKey(orgId) })
      toast.success("Invitation sent")
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        toast.error(
          "Already invited",
          "A pending invitation exists for this email.",
        )
      } else {
        toast.error(
          "Invite failed",
          err instanceof Error ? err.message : String(err),
        )
      }
    },
  })

  const cancel = useMutation({
    mutationFn: (inviteId: string) => cancelInvitation(orgId, inviteId),
    onSuccess: () => qc.invalidateQueries({ queryKey: invitesKey(orgId) }),
  })

  return (
    <div className="rounded border border-rule bg-surface">
      <header className="flex items-center justify-between gap-3 border-b border-rule px-4 py-2">
        <h2 className="font-mono text-caption uppercase tracking-wider text-ink-muted">
          Pending invitations · {invitations.length}
        </h2>
        {canInvite && !showForm && (
          <button
            type="button"
            onClick={() => setShowForm(true)}
            className="rounded-sm bg-accent-500 px-2 py-1 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600"
          >
            + Invite
          </button>
        )}
      </header>

      {showForm && canInvite && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (!email.trim()) return
            invite.mutate()
          }}
          className="flex flex-wrap items-end gap-2 border-b border-dashed border-rule px-4 py-3"
        >
          <label className="min-w-[200px] flex-1">
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Email
            </span>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="teammate@company.dev"
              className="mt-1 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            />
          </label>
          <label>
            <span className="font-mono text-micro uppercase tracking-wider text-ink-muted">
              Role
            </span>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value as "admin" | "member")}
              className="mt-1 block rounded-sm border border-rule bg-paper px-2 py-1 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            >
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={invite.isPending}
            className="h-[34px] rounded-sm bg-accent-500 px-3 font-mono text-caption font-semibold uppercase tracking-wider text-paper hover:bg-accent-600 disabled:opacity-60"
          >
            {invite.isPending ? "Sending…" : "Send"}
          </button>
          <button
            type="button"
            onClick={() => {
              setShowForm(false)
              setEmail("")
            }}
            className="h-[34px] rounded-sm border border-rule px-3 font-mono text-caption text-ink-muted hover:bg-sunken"
          >
            Cancel
          </button>
        </form>
      )}

      <ul className="divide-y divide-dashed divide-rule">
        {invitations.length === 0 ? (
          <li className="px-4 py-3 text-caption text-ink-muted">
            {canInvite
              ? "No pending invitations."
              : "Upgrade to invite teammates."}
          </li>
        ) : (
          invitations.map((i) => (
            <li
              key={i.id}
              className="flex items-center justify-between gap-3 px-4 py-2"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-ink">{i.email}</p>
                <p className="font-mono text-micro text-ink-faint">
                  role {i.role} · expires {i.expires_at.slice(0, 10)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => cancel.mutate(i.id)}
                className="rounded-sm border border-rule px-2 py-1 font-mono text-micro text-ink-faint hover:bg-sunken hover:text-ink"
              >
                Cancel
              </button>
            </li>
          ))
        )}
      </ul>
    </div>
  )
}
