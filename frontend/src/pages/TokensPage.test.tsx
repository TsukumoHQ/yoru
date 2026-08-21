import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TokensPage } from "./TokensPage"
import { Toaster, clearToasts } from "../components/Toaster"
import {
  ApiError,
  apiFetch,
  listMyTokens,
  listServiceTokens,
  revokeMyToken,
  revokeServiceToken,
  type ServiceTokenItem,
  type UserTokenItem,
} from "../lib/api"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    apiFetch: vi.fn(),
    listMyTokens: vi.fn(),
    listServiceTokens: vi.fn(),
    createServiceToken: vi.fn(),
    revokeMyToken: vi.fn(),
    revokeServiceToken: vi.fn(),
  }
})

const mockedApiFetch = vi.mocked(apiFetch)
const mockedListMyTokens = vi.mocked(listMyTokens)
const mockedListServiceTokens = vi.mocked(listServiceTokens)
const mockedRevokeMyToken = vi.mocked(revokeMyToken)
const mockedRevokeServiceToken = vi.mocked(revokeServiceToken)

const TOKEN: UserTokenItem = {
  id: "tok_1",
  label: "MacBook Pro",
  machine_hostname: "alices-macbook.local",
  created_at: "2026-08-01T00:00:00Z",
  last_used_at: "2026-08-20T00:00:00Z",
  revoked_at: null,
}

// Same hostname as TOKEN, distinct id/label — a second identity paired from
// the same machine (e.g. a re-pair after `yoru init` ran twice).
const STALE_TOKEN: UserTokenItem = {
  id: "tok_stale",
  label: "Old pairing",
  machine_hostname: "alices-macbook.local",
  created_at: "2026-05-01T00:00:00Z",
  last_used_at: "2026-06-01T00:00:00Z",
  revoked_at: null,
}

const NEVER_USED_TOKEN: UserTokenItem = {
  id: "tok_never",
  label: "New CI box",
  machine_hostname: "ci-runner-3.local",
  created_at: "2026-08-19T00:00:00Z",
  last_used_at: null,
  revoked_at: null,
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <TokensPage />
        <Toaster />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedApiFetch.mockReset()
  mockedListMyTokens.mockReset()
  mockedListServiceTokens.mockReset()
  mockedRevokeMyToken.mockReset()
  mockedRevokeServiceToken.mockReset()
  mockedApiFetch.mockResolvedValue({ items: [] })
  mockedListServiceTokens.mockResolvedValue([])
  vi.spyOn(window, "confirm").mockReturnValue(true)
  clearToasts()
})

describe("TokensPage", () => {
  it("lists the caller's personal tokens", async () => {
    mockedListMyTokens.mockResolvedValueOnce([TOKEN])
    renderPage()

    expect(await screen.findByText("MacBook Pro")).toBeInTheDocument()
    expect(screen.getByText("My machines · 1")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Revoke" })).toBeInTheDocument()
  })

  it("shows the empty state when there are no personal tokens", async () => {
    mockedListMyTokens.mockResolvedValueOnce([])
    renderPage()

    expect(await screen.findByText("No personal tokens yet")).toBeInTheDocument()
  })

  it("shows each device's machine hostname, distinguishing two identities on the same machine", async () => {
    mockedListMyTokens.mockResolvedValueOnce([TOKEN, STALE_TOKEN])
    renderPage()

    await screen.findByText("MacBook Pro")
    await screen.findByText("Old pairing")
    expect(screen.getAllByText("alices-macbook.local")).toHaveLength(2)
  })

  it("puts never-used and stale devices ahead of an active one, exception-first", async () => {
    // API order: active, stale, never-used — rendering must NOT follow it.
    mockedListMyTokens.mockResolvedValueOnce([TOKEN, STALE_TOKEN, NEVER_USED_TOKEN])
    renderPage()

    const rows = await screen.findAllByRole("listitem")
    const order = rows.map((r) => r.textContent)
    const neverIdx = order.findIndex((t) => t?.includes("New CI box"))
    const staleIdx = order.findIndex((t) => t?.includes("Old pairing"))
    const activeIdx = order.findIndex((t) => t?.includes("MacBook Pro"))

    expect(neverIdx).toBeLessThan(staleIdx)
    expect(staleIdx).toBeLessThan(activeIdx)
    expect(screen.getByText("never used")).toBeInTheDocument()
    expect(screen.getByText(/inactive \d+d/)).toBeInTheDocument()
  })

  it("shows a clean message (not a raw error) when a personal-token revoke loses the cross-tab race", async () => {
    // Losing the race still invalidates the list — the refetch reflects the
    // token now being gone (revoked by whoever won).
    mockedListMyTokens.mockResolvedValueOnce([TOKEN]).mockResolvedValueOnce([])
    mockedRevokeMyToken.mockRejectedValueOnce(new ApiError(409, "token already revoked"))
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Revoke" }))

    expect(await screen.findByText("Already revoked")).toBeInTheDocument()
    expect(screen.queryByText("token already revoked")).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("shows a clean message when a service-token revoke loses the cross-tab race", async () => {
    mockedListMyTokens.mockResolvedValueOnce([])
    mockedApiFetch.mockResolvedValueOnce({
      items: [{ id: "org_1", name: "Acme", slug: "acme", type: "team" }],
    })
    const serviceToken: ServiceTokenItem = {
      id: "svc_1",
      org_id: "org_1",
      label: "ci-runner",
      created_at: "2026-08-01T00:00:00Z",
      revoked_at: null,
    }
    mockedListServiceTokens.mockResolvedValueOnce([serviceToken])
    mockedRevokeServiceToken.mockRejectedValueOnce(new ApiError(404, "service token not found"))
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "Revoke" }))

    expect(await screen.findByText("Already revoked")).toBeInTheDocument()
    expect(screen.queryByText("service token not found")).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })
})
