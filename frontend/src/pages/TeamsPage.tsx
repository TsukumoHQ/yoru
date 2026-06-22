import { useState, type FormEvent } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  listUsers, createUser, setUserRole, deleteUser,
  listGroups, createGroup, addGroupMember,
  getEmailStatus, setEmailSettings,
  getRetention, setRetention, pruneNow,
  type AdminUser, type Group,
} from "../lib/admin-api"

const CARD = "rounded border border-rule bg-surface p-5"
const INPUT = "mt-1 w-full rounded border border-rule bg-paper px-3 py-2 text-sm text-ink outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
const LABEL = "font-mono text-micro uppercase tracking-wider text-ink-faint"
const BTN = "rounded bg-accent-500 px-3 py-2 text-sm font-medium text-primary-950 hover:bg-accent-400 disabled:opacity-50"
const BTN_GHOST = "rounded border border-rule px-2 py-1 text-caption text-ink-muted hover:bg-sunken"

export function TeamsPage() {
  const qc = useQueryClient()
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: listUsers })
  const groups = useQuery({ queryKey: ["admin", "groups"], queryFn: listGroups })
  const email = useQuery({ queryKey: ["admin", "email"], queryFn: getEmailStatus })

  const isForbidden = (users.error as Error | null)?.message?.includes("403")
  if (isForbidden) {
    return (
      <div className={CARD}>
        <p className="text-sm text-ink-muted">Admin access required to manage teams.</p>
      </div>
    )
  }

  return (
    <div className="space-y-8">
      <h1 className="font-mono text-lg font-semibold text-ink">Teams &amp; access</h1>
      <UsersSection users={users.data ?? []} onChange={() => qc.invalidateQueries({ queryKey: ["admin"] })} />
      <GroupsSection groups={groups.data ?? []} users={users.data ?? []} onChange={() => qc.invalidateQueries({ queryKey: ["admin", "groups"] })} />
      <EmailSection configured={email.data?.configured ?? false} host={email.data?.smtp_host} />
      <RetentionSection />
    </div>
  )
}

function RetentionSection() {
  const qc = useQueryClient()
  const ret = useQuery({ queryKey: ["admin", "retention"], queryFn: getRetention })
  const [days, setDays] = useState<number | null>(null)
  const value = days ?? ret.data?.retention_days ?? 0
  const save = useMutation({
    mutationFn: () => setRetention(value),
    onSuccess: () => { setDays(null); qc.invalidateQueries({ queryKey: ["admin", "retention"] }) },
  })
  const prune = useMutation({
    mutationFn: pruneNow,
    onSuccess: (r) => alert(`Pruned ${r.pruned_sessions} sessions / ${r.pruned_events} events.${r.note ? " " + r.note : ""}`),
  })
  return (
    <section className={CARD}>
      <h2 className="font-mono text-sm font-semibold text-ink">Data retention</h2>
      <p className="mt-1 text-caption text-ink-muted">Delete sessions older than N days. 0 = keep forever (default).</p>
      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="block"><span className={LABEL}>Retention (days)</span>
          <input type="number" min={0} value={value} onChange={(e) => setDays(Number(e.target.value))} className={INPUT + " w-32"} /></label>
        <button onClick={() => save.mutate()} disabled={save.isPending} className={BTN}>Save policy</button>
        <button onClick={() => { if (confirm("Delete all data older than the policy now?")) prune.mutate() }} disabled={prune.isPending} className={BTN_GHOST}>Prune now</button>
      </div>
    </section>
  )
}

function UsersSection({ users, onChange }: { users: AdminUser[]; onChange: () => void }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [first, setFirst] = useState("")
  const [err, setErr] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => createUser({ email, password, first_name: first || undefined }),
    onSuccess: () => { setEmail(""); setPassword(""); setFirst(""); setErr(null); onChange() },
    onError: (e: Error) => setErr(e.message),
  })
  const role = useMutation({ mutationFn: (v: { id: string; role: string }) => setUserRole(v.id, v.role), onSuccess: onChange })
  const del = useMutation({ mutationFn: (id: string) => deleteUser(id), onSuccess: onChange })

  function submit(e: FormEvent) { e.preventDefault(); create.mutate() }

  return (
    <section className={CARD}>
      <h2 className="font-mono text-sm font-semibold text-ink">Users</h2>
      <p className="mt-1 text-caption text-ink-muted">Provision accounts directly — no email required.</p>
      <table className="mt-4 w-full text-sm">
        <thead><tr className="text-left font-mono text-micro uppercase tracking-wider text-ink-faint">
          <th className="pb-2">Email</th><th className="pb-2">Role</th><th className="pb-2"></th>
        </tr></thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className="border-t border-rule/50">
              <td className="py-2 text-ink">{u.email}</td>
              <td className="py-2">
                <select
                  value={u.role}
                  onChange={(e) => role.mutate({ id: u.id, role: e.target.value })}
                  className="rounded border border-rule bg-paper px-2 py-1 text-caption"
                >
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td className="py-2 text-right">
                <button className={BTN_GHOST} onClick={() => { if (confirm(`Delete ${u.email}?`)) del.mutate(u.id) }}>Delete</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <form onSubmit={submit} className="mt-5 grid grid-cols-1 gap-3 border-t border-dashed border-rule pt-4 sm:grid-cols-4 sm:items-end">
        <label className="block sm:col-span-2"><span className={LABEL}>Email</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={INPUT} placeholder="user@firm.io" /></label>
        <label className="block"><span className={LABEL}>First name</span>
          <input value={first} onChange={(e) => setFirst(e.target.value)} className={INPUT} /></label>
        <label className="block"><span className={LABEL}>Password</span>
          <input type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} className={INPUT} placeholder="min 8 chars" /></label>
        <div className="sm:col-span-4">
          <button type="submit" disabled={create.isPending} className={BTN}>{create.isPending ? "Creating…" : "Add user"}</button>
          {err && <span className="ml-3 text-caption text-flag-env">{err}</span>}
        </div>
      </form>
    </section>
  )
}

function GroupsSection({ groups, users, onChange }: { groups: Group[]; users: AdminUser[]; onChange: () => void }) {
  const [name, setName] = useState("")
  const create = useMutation({ mutationFn: () => createGroup({ name }), onSuccess: () => { setName(""); onChange() } })
  const addMember = useMutation({ mutationFn: (v: { g: string; u: string }) => addGroupMember(v.g, v.u), onSuccess: onChange })

  return (
    <section className={CARD}>
      <h2 className="font-mono text-sm font-semibold text-ink">Teams</h2>
      <p className="mt-1 text-caption text-ink-muted">Members of a team see each other's sessions. Teams are walled off from one another.</p>
      <ul className="mt-4 space-y-2">
        {groups.map((g) => (
          <li key={g.id} className="flex items-center justify-between border-t border-rule/50 py-2 text-sm">
            <span className="text-ink">{g.name} <span className="text-ink-faint">· {g.member_count ?? 0} members</span></span>
            <select
              defaultValue=""
              onChange={(e) => { if (e.target.value) addMember.mutate({ g: g.id, u: e.target.value }) }}
              className="rounded border border-rule bg-paper px-2 py-1 text-caption"
            >
              <option value="">+ add member…</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)}
            </select>
          </li>
        ))}
        {groups.length === 0 && <li className="py-2 text-caption text-ink-muted">No teams yet.</li>}
      </ul>
      <form onSubmit={(e) => { e.preventDefault(); create.mutate() }} className="mt-4 flex gap-2 border-t border-dashed border-rule pt-4">
        <input value={name} onChange={(e) => setName(e.target.value)} className={INPUT + " mt-0"} placeholder="New team name" required />
        <button type="submit" disabled={create.isPending} className={BTN}>Create</button>
      </form>
    </section>
  )
}

function EmailSection({ configured, host }: { configured: boolean; host?: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ smtp_host: "", smtp_port: 587, smtp_username: "", smtp_password: "", smtp_from_email: "" })
  const save = useMutation({
    mutationFn: () => setEmailSettings(f),
    onSuccess: () => { setOpen(false); qc.invalidateQueries({ queryKey: ["admin", "email"] }) },
  })
  return (
    <section className={CARD}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="font-mono text-sm font-semibold text-ink">Email (SMTP)</h2>
          <p className="mt-1 text-caption text-ink-muted">
            {configured ? `Configured · ${host}` : "Not configured — invitations send in-app only."}
          </p>
        </div>
        <button className={BTN_GHOST} onClick={() => setOpen((o) => !o)}>{open ? "Cancel" : "Configure"}</button>
      </div>
      {open && (
        <form onSubmit={(e) => { e.preventDefault(); save.mutate() }} className="mt-4 grid grid-cols-1 gap-3 border-t border-dashed border-rule pt-4 sm:grid-cols-2">
          <label className="block"><span className={LABEL}>SMTP host</span><input required value={f.smtp_host} onChange={(e) => setF({ ...f, smtp_host: e.target.value })} className={INPUT} placeholder="smtp.example.com" /></label>
          <label className="block"><span className={LABEL}>Port</span><input type="number" value={f.smtp_port} onChange={(e) => setF({ ...f, smtp_port: Number(e.target.value) })} className={INPUT} /></label>
          <label className="block"><span className={LABEL}>Username</span><input required value={f.smtp_username} onChange={(e) => setF({ ...f, smtp_username: e.target.value })} className={INPUT} /></label>
          <label className="block"><span className={LABEL}>Password</span><input type="password" required value={f.smtp_password} onChange={(e) => setF({ ...f, smtp_password: e.target.value })} className={INPUT} /></label>
          <label className="block sm:col-span-2"><span className={LABEL}>From email</span><input type="email" required value={f.smtp_from_email} onChange={(e) => setF({ ...f, smtp_from_email: e.target.value })} className={INPUT} placeholder="noreply@firm.io" /></label>
          <div className="sm:col-span-2"><button type="submit" disabled={save.isPending} className={BTN}>{save.isPending ? "Saving…" : "Save SMTP"}</button></div>
        </form>
      )}
    </section>
  )
}
