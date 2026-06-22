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
