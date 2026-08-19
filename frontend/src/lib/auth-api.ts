// Cookie-based auth client. No tokens ever touch JS — the backend sets
// HttpOnly cookies (`rcpt_session` + `rcpt_refresh`) on signin and the
// browser attaches them automatically on subsequent requests when we pass
// `credentials: 'include'`.
//
// CSRF: the backend also sets a non-HttpOnly `rcpt_csrf` cookie; on every
// mutating request we echo its value as `X-CSRF-Token` (double-submit).
// See backend/apps/api/api/middleware/csrf.py.

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8002/api/v1"

export interface AuthUser {
  id: string
  email: string
  first_name?: string | null
  last_name?: string | null
  avatar_url?: string | null
  // Studio super-admin (deployer) — derived server-side from profiles.role
  // =='admin' and exposed as a bool so the role enum never reaches the client
  // (M3 contract, msg 1301af63). Gates the cross-org org switcher. Absent on
  // older backends → treated as false (no cross-org UI).
  is_super_admin?: boolean
}

export class AuthError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status} ${detail}`)
  }
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`))
  return match ? decodeURIComponent(match[1]) : null
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase()
  const headers = new Headers(init?.headers)
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json")
  }
  const mutating = method !== "GET" && method !== "HEAD" && method !== "OPTIONS"
  if (mutating) {
    const csrf = readCookie("rcpt_csrf")
    if (csrf) headers.set("X-CSRF-Token", csrf)
  }
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  })
  const text = await res.text()
  if (!res.ok) {
    let detail = text
    try {
      detail = (JSON.parse(text) as { detail?: string }).detail ?? text
    } catch {
      // plain-text body
    }
    throw new AuthError(res.status, detail || res.statusText)
  }
  if (res.status === 204 || !text) return undefined as T
  return JSON.parse(text) as T
}

interface UserEnvelope {
  user: AuthUser
}

export async function signup(payload: {
  email: string
  password: string
  first_name?: string
  last_name?: string
  invitation_token?: string
}): Promise<AuthUser> {
  const { user } = await request<UserEnvelope>("/auth/signup", {
    method: "POST",
    body: JSON.stringify(payload),
  })
  return user
}

export async function signin(email: string, password: string): Promise<AuthUser> {
  const { user } = await request<UserEnvelope>("/auth/signin", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  })
  return user
}

export async function signout(): Promise<void> {
  await request<void>("/auth/session/signout", { method: "POST" })
}

export async function refresh(): Promise<AuthUser> {
  const { user } = await request<UserEnvelope>("/auth/session/refresh", {
    method: "POST",
  })
  return user
}

export async function getMe(): Promise<AuthUser | null> {
  try {
    const { user } = await request<UserEnvelope>("/auth/session/me")
    return user
  } catch (err) {
    if (err instanceof AuthError && err.status === 401) return null
    throw err
  }
}
