import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { OrgIdentitiesPage, identityException, multiMachineUsers } from "./OrgIdentitiesPage"
import { ApiError, getOrgIdentities, listOrganizations } from "../lib/api"
import type { OrgIdentityItem } from "../lib/api"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    listOrganizations: vi.fn(),
    getOrgIdentities: vi.fn(),
  }
})

const mockedListOrganizations = vi.mocked(listOrganizations)
const mockedGetOrgIdentities = vi.mocked(getOrgIdentities)

const ORG = { id: "org_1", name: "Acme" }

function item(overrides: Partial<OrgIdentityItem>): OrgIdentityItem {
  return {
    org_id: "org_1",
    user: "alice@acme.dev",
    id: "id_1",
    identity_label: "laptop",
    machine_hostname: "alice-mbp",
    created_at: "2026-08-01T00:00:00Z",
    last_used_at: "2026-08-20T00:00:00Z",
    status: "active",
    ...overrides,
  }
}

const EXPIRED = item({ id: "id_exp", user: "alice@acme.dev", status: "expired" })
const NEVER_USED = item({
  id: "id_never",
  user: "bob@acme.dev",
  identity_label: "ci-box",
  machine_hostname: "bob-ci",
  last_used_at: null,
})
const STALE = item({
  id: "id_stale",
  user: "carol@acme.dev",
  identity_label: "old laptop",
  machine_hostname: "carol-old",
  created_at: "2026-05-01T00:00:00Z",
  last_used_at: "2026-06-01T00:00:00Z",
})
const MULTI_A = item({
  id: "id_multi_a",
  user: "dave@acme.dev",
  identity_label: "laptop",
  machine_hostname: "dave-mac",
})
const MULTI_B = item({
  id: "id_multi_b",
  user: "dave@acme.dev",
  identity_label: "desktop",
  machine_hostname: "dave-desktop",
})
const HEALTHY = item({ id: "id_healthy", user: "eve@acme.dev", machine_hostname: "eve-mac" })
const REVOKED = item({
  id: "id_revoked",
  user: "frank@acme.dev",
  machine_hostname: "frank-old",
  status: "revoked",
})

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <OrgIdentitiesPage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedListOrganizations.mockReset()
  mockedGetOrgIdentities.mockReset()
  mockedListOrganizations.mockResolvedValue([ORG])
})

describe("identityException", () => {
  it("flags expired, never-used, and stale ahead of multi-machine, and clears healthy/revoked", () => {
    const multi = multiMachineUsers([MULTI_A, MULTI_B])
    expect(identityException(EXPIRED, multi)).toBe("expired")
    expect(identityException(NEVER_USED, multi)).toBe("never-used")
    expect(identityException(STALE, multi)).toBe("stale")
    expect(identityException(MULTI_A, multi)).toBe("multi-machine")
    expect(identityException(HEALTHY, multi)).toBeNull()
    expect(identityException(REVOKED, multi)).toBeNull()
  })
})

describe("multiMachineUsers", () => {
  it("flags a user with 2+ distinct hostnames, ignoring revoked identities", () => {
    expect(multiMachineUsers([MULTI_A, MULTI_B]).has("dave@acme.dev")).toBe(true)
    expect(multiMachineUsers([HEALTHY]).has("eve@acme.dev")).toBe(false)
    // Same hostname twice (re-fetch of the same device) isn't "multi-machine".
    const sameHost = [MULTI_A, { ...MULTI_A, id: "id_multi_a2" }]
    expect(multiMachineUsers(sameHost).has("dave@acme.dev")).toBe(false)
  })
})

describe("OrgIdentitiesPage", () => {
  it("surfaces expired/never-used/stale/multi-machine exceptions ranked ahead of healthy identities", async () => {
    mockedGetOrgIdentities.mockResolvedValueOnce([
      HEALTHY,
      MULTI_A,
      MULTI_B,
      STALE,
      NEVER_USED,
      EXPIRED,
    ])
    renderPage()

    const exceptionsSection = await screen.findByRole("region", { name: "Identity exceptions" })
    const rows = within(exceptionsSection).getAllByRole("listitem")
    const order = rows.map((r) => r.textContent ?? "")
    const expIdx = order.findIndex((t) => t.includes("expired") && t.includes("alice@acme.dev"))
    const neverIdx = order.findIndex((t) => t.includes("never used") && t.includes("bob@acme.dev"))
    const staleIdx = order.findIndex((t) => t.includes("stale") && t.includes("carol@acme.dev"))
    const multiIdx = order.findIndex((t) => t.includes("multi-machine") && t.includes("dave@acme.dev"))

    expect(expIdx).toBeGreaterThanOrEqual(0)
    expect(expIdx).toBeLessThan(neverIdx)
    expect(neverIdx).toBeLessThan(staleIdx)
    expect(staleIdx).toBeLessThan(multiIdx)
    // eve (HEALTHY) never appears in the exceptions list at all.
    expect(order.some((t) => t.includes("eve@acme.dev"))).toBe(false)
  })

  it("groups by dev and collapses revoked identities behind a toggle", async () => {
    mockedGetOrgIdentities.mockResolvedValueOnce([HEALTHY, REVOKED])
    renderPage()

    expect(await screen.findByText("By dev · 1")).toBeInTheDocument()
    expect(screen.queryByText("frank@acme.dev")).not.toBeInTheDocument()
    expect(screen.getByText("+ 1 revoked")).toBeInTheDocument()
  })

  it("shows a clean not-authorized state on 403, never raw identity data", async () => {
    mockedGetOrgIdentities.mockRejectedValueOnce(new ApiError(403, "not an org admin"))
    renderPage()

    expect(await screen.findByText("Not authorized")).toBeInTheDocument()
    expect(screen.queryByText("not an org admin")).not.toBeInTheDocument()
    expect(screen.queryByText("alice@acme.dev")).not.toBeInTheDocument()
  })

  it("shows the empty-orgs state when the caller belongs to no org", async () => {
    mockedListOrganizations.mockReset()
    mockedListOrganizations.mockResolvedValue([])
    renderPage()

    expect(await screen.findByText("No organizations yet")).toBeInTheDocument()
    expect(mockedGetOrgIdentities).not.toHaveBeenCalled()
  })
})
