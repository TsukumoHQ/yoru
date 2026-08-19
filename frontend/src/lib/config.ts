// Runtime instance configuration — lets the SPA hide hosted-only UI
// (billing, upgrade CTAs, multi-tenant orgs) on a self-hosted deployment.
import { useQuery } from "@tanstack/react-query"

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8002/api/v1"

export interface InstanceConfig {
  billing_enabled: boolean
  auth_provider: string
  instance_name: string
  single_org: boolean
}

const DEFAULT_CONFIG: InstanceConfig = {
  billing_enabled: false,
  auth_provider: "local",
  instance_name: "Yoru",
  single_org: true,
}

export async function getInstanceConfig(): Promise<InstanceConfig> {
  const res = await fetch(`${API_BASE}/config`, { credentials: "include" })
  if (!res.ok) return DEFAULT_CONFIG
  return (await res.json()) as InstanceConfig
}

export function useInstanceConfig(): InstanceConfig {
  const { data } = useQuery({
    queryKey: ["instance", "config"],
    queryFn: getInstanceConfig,
    staleTime: 5 * 60_000,
    retry: 0,
    // Self-host defaults until the call resolves — never flash billing UI.
    placeholderData: DEFAULT_CONFIG,
  })
  return data ?? DEFAULT_CONFIG
}

// True once GET /config has actually resolved (success or error). While it's
// pending, useInstanceConfig returns DEFAULT_CONFIG (single_org=true) as
// placeholder data — callers that make a DESTRUCTIVE decision on the config
// (e.g. clearing persisted state when the instance looks single-org) must wait
// for this, or they'll act on the placeholder during the initial-load window.
// Shares the same query cache key, so it adds no extra request.
export function useInstanceConfigResolved(): boolean {
  const { isSuccess } = useQuery({
    queryKey: ["instance", "config"],
    queryFn: getInstanceConfig,
    staleTime: 5 * 60_000,
    retry: 0,
    placeholderData: DEFAULT_CONFIG,
  })
  // Only a genuine /config response counts as resolved. A transient network
  // error still serves the single_org=true placeholder, so treating isError as
  // resolved would let a reload blip wipe a super-admin's persisted org
  // selection (review-yoru-mt-orgscope-signout notice). Fail toward "not yet
  // known" instead — the selection survives the blip and self-heals on refetch.
  return isSuccess
}
