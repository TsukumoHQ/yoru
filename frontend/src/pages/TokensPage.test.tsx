import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TokensPage } from "./TokensPage"
import { apiFetch, listMyTokens, listServiceTokens, type UserTokenItem } from "../lib/api"

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
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedApiFetch.mockReset()
  mockedListMyTokens.mockReset()
  mockedListServiceTokens.mockReset()
  mockedApiFetch.mockResolvedValue({ items: [] })
  mockedListServiceTokens.mockResolvedValue([])
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
})
