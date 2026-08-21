import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { FeedPage } from "./FeedPage"
import { listActivity } from "../lib/api"
import type { ActivityItem } from "../types/receipt"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    listActivity: vi.fn(),
    // FilterBar (rendered by FeedPage) fetches workspaces for its filter
    // dropdown — mock it so the real apiFetch (and its 401 redirect path)
    // never fires in jsdom.
    listWorkspaces: vi.fn().mockResolvedValue([]),
  }
})

const mockedListActivity = vi.mocked(listActivity)

const ACTIVITY_ITEM: ActivityItem = {
  id: 1,
  session_id: "sess_1",
  at: "2026-08-21T00:00:00Z",
  user_email: "alice@acme.dev",
  agent: "claude-code",
  kind: "tool_use",
  tool: "Bash",
  path: null,
  flags: [],
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <FeedPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedListActivity.mockReset()
})

describe("FeedPage", () => {
  it("defaults to exception-first: flag_only requested, empty flags to 'no exceptions'", async () => {
    mockedListActivity.mockResolvedValueOnce({ items: [] })
    renderPage()

    expect(screen.getByRole("heading", { name: "Activity" })).toBeInTheDocument()
    expect(await screen.findByText("No exceptions right now")).toBeInTheDocument()
    expect(mockedListActivity).toHaveBeenCalledWith(
      expect.objectContaining({ flag_only: true }),
    )
  })

  it("'View all activity' toggles off flag_only and reveals the full feed", async () => {
    mockedListActivity.mockResolvedValueOnce({ items: [] })
    mockedListActivity.mockResolvedValueOnce({ items: [ACTIVITY_ITEM] })
    renderPage()
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "View all activity →" }))

    expect(await screen.findByRole("link", { name: /alice@acme.dev/ })).toBeInTheDocument()
    expect(mockedListActivity).toHaveBeenLastCalledWith(
      expect.objectContaining({ flag_only: false }),
    )
  })

  it("surfaces an error banner with retry when the feed fails to load", async () => {
    mockedListActivity.mockRejectedValueOnce(new Error("network down"))
    renderPage()

    expect(await screen.findByRole("alert")).toHaveTextContent("network down")
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
  })
})
