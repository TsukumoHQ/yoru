import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { OrganizationsPage } from "./OrganizationsPage"
import { apiFetch } from "../lib/api"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    apiFetch: vi.fn(),
  }
})

const mockedApiFetch = vi.mocked(apiFetch)

const ORG = {
  id: "org_1",
  name: "Acme",
  slug: "acme",
  type: "team" as const,
  owner_id: "user_1",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
}

function mockOrgsResponse(orgsResponse: unknown) {
  mockedApiFetch.mockImplementation(async (url: string) => {
    if (url === "/me/organizations") return orgsResponse
    if (url === "/me/subscription") return null
    throw new Error(`unexpected apiFetch call: ${url}`)
  })
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <OrganizationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedApiFetch.mockReset()
})

describe("OrganizationsPage / listOrgs response shapes", () => {
  it("renders the org when GET /me/organizations returns {items:[...]}", async () => {
    mockOrgsResponse({ items: [ORG] })
    renderPage()

    expect((await screen.findAllByText("Acme")).length).toBeGreaterThan(0)
  })

  it("shows the empty state, not a crash, for []", async () => {
    mockOrgsResponse([])
    renderPage()

    expect(await screen.findByText("No organizations yet")).toBeInTheDocument()
  })

  it("shows the empty state, not a crash, for {}", async () => {
    mockOrgsResponse({})
    renderPage()

    expect(await screen.findByText("No organizations yet")).toBeInTheDocument()
  })

  it("shows the empty state, not a crash, for null", async () => {
    mockOrgsResponse(null)
    renderPage()

    expect(await screen.findByText("No organizations yet")).toBeInTheDocument()
  })

  it("shows the empty state, not a crash, for an unexpected field name ({organizations:[...]})", async () => {
    mockOrgsResponse({ organizations: [ORG] })
    renderPage()

    expect(await screen.findByText("No organizations yet")).toBeInTheDocument()
  })

  it("shows the empty state, not a crash, when GET /me/organizations throws", async () => {
    mockedApiFetch.mockImplementation(async (url: string) => {
      if (url === "/me/organizations") throw new Error("boom")
      if (url === "/me/subscription") return null
      throw new Error(`unexpected apiFetch call: ${url}`)
    })
    renderPage()

    expect(await screen.findByText("No organizations yet")).toBeInTheDocument()
  })
})
