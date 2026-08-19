// Studio super-admin org selection (yoru multi-tenant, design doc trovex:44a3774a).
//
// A studio super-admin (auth `is_super_admin`) can scope every audit read to a
// single client-org by selecting it here; the selection is sent as the
// `X-Organization-Id` request header (see `orgHeaders` in lib/api.ts).
//
// Semantics (backend contract, msg 1301af63):
//   - super-admin, no selection  → header omitted → sees ALL orgs
//   - super-admin, org selected   → header = org id → sees only that org
//   - regular member              → header is a no-op (backend always scopes to
//     the token's org); selecting another org returns 404 (indistinguishable).
//
// A tiny external store (not a context) so the non-React api helpers can read
// the current selection synchronously when building request headers, while
// components stay reactive via useSyncExternalStore.
import { useSyncExternalStore } from "react"

const STORAGE_KEY = "yoru.selectedOrgId"

function readInitial(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || null
  } catch {
    return null
  }
}

let currentOrgId: string | null = readInitial()
const listeners = new Set<() => void>()

/** Synchronous read for request-header construction (non-React callers). */
export function getSelectedOrgId(): string | null {
  return currentOrgId
}

export function setSelectedOrgId(id: string | null): void {
  if (id === currentOrgId) return
  currentOrgId = id
  try {
    if (id) window.localStorage.setItem(STORAGE_KEY, id)
    else window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Storage can throw in private mode — the in-memory value still holds.
  }
  for (const l of listeners) l()
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

/** Reactive selection for components (re-renders on change). */
export function useSelectedOrgId(): string | null {
  return useSyncExternalStore(subscribe, getSelectedOrgId, getSelectedOrgId)
}
