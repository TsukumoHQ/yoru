import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SessionsListPage } from "./SessionsListPage"
import { listSessions } from "../lib/api"
import type { Session, SessionList } from "../types/receipt"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    listSessions: vi.fn(),
    listWorkspaces: vi.fn().mockResolvedValue([]),
    listOrganizations: vi.fn().mockResolvedValue([]),
  }
})

const mockedListSessions = vi.mocked(listSessions)

const TOTAL = 60

function session(i: number): Session {
  return {
    id: `sess_${i}`,
    user_email: `user${i}@acme.dev`,
    started_at: "2026-08-20T10:00:00Z",
    ended_at: "2026-08-20T10:05:00Z",
    duration_ms: 300000,
    tool_count: 1,
    cost_usd: 1,
    tokens_input: 10,
    tokens_output: 10,
    flag_count: 0,
    flags: [],
  } as unknown as Session
}

// Two-page fleet of TOTAL sessions, 50 per page (offset-sliced like the API).
function fleetResponse(offset: number, limit: number): SessionList {
  const items = Array.from({ length: TOTAL }, (_, i) => session(i)).slice(offset, offset + limit)
  return { items, total: TOTAL } as SessionList
}

function renderPage(url = "/?flag=0") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[url]}>
        <SessionsListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedListSessions.mockReset()
  mockedListSessions.mockImplementation(async (f) => fleetResponse(f.offset ?? 0, f.limit ?? 50))
})

describe("SessionsListPage pagination", () => {
  it("sends limit and offset on the first fetch", async () => {
    renderPage()
    await screen.findByText("Page 1 of 2 · 60 sessions")
    expect(mockedListSessions).toHaveBeenCalledWith(expect.objectContaining({ limit: 50, offset: 0 }))
  })

  it("renders Prev disabled on page 1 and Next enabled", async () => {
    renderPage()
    await screen.findByText("Page 1 of 2 · 60 sessions")
    expect(screen.getByRole("button", { name: "Prev" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Next" })).toBeEnabled()
  })

  it("reaches the last page, shows the remaining rows, and disables Next", async () => {
    renderPage()
    await screen.findByText("Page 1 of 2 · 60 sessions")
    await userEvent.click(screen.getByRole("button", { name: "Next" }))
    await screen.findByText("Page 2 of 2 · 60 sessions")
    expect(mockedListSessions).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 50, offset: 50 }))
    expect(await screen.findByText("user59@acme.dev")).toBeInTheDocument()
    expect(screen.queryByText("user0@acme.dev")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Prev" })).toBeEnabled()
  })

  it("resets offset to 0 when a filter changes", async () => {
    renderPage("/?flag=0&page=2")
    await screen.findByText("Page 2 of 2 · 60 sessions")
    expect(mockedListSessions).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }))
    await userEvent.click(screen.getByRole("button", { name: "7d" }))
    await waitFor(() =>
      expect(mockedListSessions).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 })),
    )
    await screen.findByText("Page 1 of 2 · 60 sessions")
  })

  it("clamps a stale page past the end to the last page", async () => {
    renderPage("/?flag=0&page=9")
    await screen.findByText("Page 2 of 2 · 60 sessions")
    expect(mockedListSessions).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }))
  })

  it("hides page controls when everything fits on one page", async () => {
    mockedListSessions.mockResolvedValue({ items: [session(0)], total: 1 } as SessionList)
    renderPage()
    await screen.findByText("user0@acme.dev")
    expect(screen.queryByRole("navigation", { name: "Sessions pagination" })).not.toBeInTheDocument()
  })
})
