// First-run onboarding client. Talks to the unauthenticated /setup/* endpoints
// that exist only until the instance has an admin account.

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8002/api/v1"

export interface SetupStatus {
  installed: boolean
  needs_setup: boolean
  auth_provider: string
  database_url: string
  database_reachable: boolean
  setup_token_required: boolean
}

export interface DbTestResult {
  ok: boolean
  url?: string
  error?: string
}

export interface InitResult {
  restart_required: boolean
  admin_email: string
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body) headers.set("Content-Type", "application/json")
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" })
  const text = await res.text()
  if (!res.ok) {
    let detail = text
    try {
      detail = (JSON.parse(text) as { detail?: string; error?: { message?: string } }).detail
        ?? (JSON.parse(text) as { error?: { message?: string } }).error?.message
        ?? text
    } catch {
      /* plain text */
    }
    throw new Error(detail || res.statusText)
  }
  return (text ? JSON.parse(text) : undefined) as T
}

export function getSetupStatus(): Promise<SetupStatus> {
  return call<SetupStatus>("/setup/status")
}

export function testDatabase(database_url: string): Promise<DbTestResult> {
  return call<DbTestResult>("/setup/database/test", {
    method: "POST",
    body: JSON.stringify({ database_url }),
  })
}

export function initInstance(payload: {
  admin_email: string
  admin_password: string
  first_name?: string
  database_url?: string
  email_mode?: string
  setup_token?: string
}): Promise<InitResult> {
  return call<InitResult>("/setup/init", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}
