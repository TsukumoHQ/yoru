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
  created_at: "2026-08-01T00:00:00Z",
  last_used_at: "2026-08-20T00:00:00Z",
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
