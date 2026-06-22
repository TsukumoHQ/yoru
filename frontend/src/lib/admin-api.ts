// Admin control-panel client (self-host instance management).
// All endpoints require role=admin; non-admins get 403.
import { apiFetch } from "./api"

export interface AdminUser {
  id: string
  email: string
  first_name?: string | null
  last_name?: string | null
  role: "user" | "admin"
  created_at: string
  last_login_at?: string | null
}

export interface Group {
  id: string
  name: string
  description?: string | null
  member_count?: number
}

export interface EmailStatus {
  configured: boolean
  provider: string
  smtp_host: string
  smtp_port: number
  smtp_username: string
  smtp_from_email: string
}

// ── users ──
export const listUsers = () => apiFetch<AdminUser[]>("/admin/instance/users")
export const createUser = (body: {
  email: string; password: string; first_name?: string; role?: string
}) => apiFetch<AdminUser>("/admin/instance/users", { method: "POST", body: JSON.stringify(body) })
export const setUserRole = (id: string, role: string) =>
  apiFetch<AdminUser>(`/admin/instance/users/${id}/role`, { method: "PATCH", body: JSON.stringify({ role }) })
export const deleteUser = (id: string) =>
  apiFetch<{ ok: boolean }>(`/admin/instance/users/${id}`, { method: "DELETE" })

// ── groups / teams ──
interface GroupList { items?: Group[] }
export const listGroups = async (): Promise<Group[]> => {
  const r = await apiFetch<GroupList | Group[]>("/admin/groups")
  return Array.isArray(r) ? r : (r.items ?? [])
}
export const createGroup = (body: { name: string; description?: string }) =>
  apiFetch<Group>("/admin/groups", { method: "POST", body: JSON.stringify(body) })
export const addGroupMember = (groupId: string, userId: string) =>
  apiFetch<unknown>(`/admin/groups/${groupId}/members`, { method: "POST", body: JSON.stringify({ user_id: userId }) })
export const removeGroupMember = (groupId: string, userId: string) =>
  apiFetch<unknown>(`/admin/groups/${groupId}/members/${userId}`, { method: "DELETE" })

// ── email / SMTP ──
export const getEmailStatus = () => apiFetch<EmailStatus>("/admin/instance/settings/email")
export const setEmailSettings = (body: {
  smtp_host: string; smtp_port: number; smtp_username: string
  smtp_password: string; smtp_from_email: string; smtp_from_name?: string
}) => apiFetch<{ ok: boolean }>("/admin/instance/settings/email", { method: "POST", body: JSON.stringify(body) })

// ── retention ──
export const getRetention = () => apiFetch<{ retention_days: number }>("/admin/instance/retention")
export const setRetention = (days: number) =>
  apiFetch<{ retention_days: number }>("/admin/instance/retention", { method: "POST", body: JSON.stringify({ days }) })
export const pruneNow = () =>
  apiFetch<{ pruned_sessions: number; pruned_events: number; note?: string }>("/admin/instance/retention/prune", { method: "POST" })
