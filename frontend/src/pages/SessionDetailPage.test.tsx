import { describe, expect, it, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Routes, Route } from "react-router-dom"
import { SessionDetailPage } from "./SessionDetailPage"
import { getSession, getSummary, verifySession, fetchCustomRuleInfo } from "../lib/api"
import type { SessionDetail, Summary, SessionEvent } from "../types/receipt"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    getSession: vi.fn(),
    getSummary: vi.fn(),
    verifySession: vi.fn(),
    fetchCustomRuleInfo: vi.fn(),
  }
})

const mockedGetSession = vi.mocked(getSession)
const mockedGetSummary = vi.mocked(getSummary)
const mockedVerifySession = vi.mocked(verifySession)
const mockedFetchCustomRuleInfo = vi.mocked(fetchCustomRuleInfo)

function event(id: string): SessionEvent {
  return {
    id,
    session_id: "sess_1",
    at: "2026-08-20T10:00:00Z",
    type: "tool_call",
    tool_name: "bash",
    text: `event ${id}`,
  }
}

function sessionFixture(): SessionDetail {
  return {
    id: "sess_1",
    user_email: "alice@acme.dev",
    started_at: "2026-08-20T10:00:00Z",
    ended_at: "2026-08-20T10:05:00Z",
    duration_ms: 300000,
    tool_count: 2,
    cost_usd: 3.5,
    tokens_input: 4000,
    tokens_output: 2000,
    flag_count: 0,
    flags: [],
    events: [event("evt_1"), event("evt_2")],
    files_changed: [],
    summary: null,
    score: {
      overall: 82,
      throughput: 80,
      reliability: 90,
      safety: 100,
      grade: "B",
      breakdown: { tool_success_rate: 95, errors: 0, unique_tools: ["bash"], files_count: 0, flag_penalty: 0, hard_ceiling: null },
    },
  } as SessionDetail
}

function summaryFixture(): Summary {
  return { text: "Fixed the auth middleware bug and shipped tests.", model: "claude-sonnet-4-6", cached_at: "2026-08-20T10:06:00Z" }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/s/sess_1"]}>
        <Routes>
          <Route path="/s/:id" element={<SessionDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedGetSession.mockReset()
  mockedGetSummary.mockReset()
  mockedVerifySession.mockReset()
  mockedFetchCustomRuleInfo.mockReset()
  mockedFetchCustomRuleInfo.mockResolvedValue({})
  mockedGetSummary.mockResolvedValue(summaryFixture())
  window.location.hash = ""
})

afterEach(() => {
  window.location.hash = ""
})

describe("SessionDetailPage", () => {
  it("leads with the synthesis: summary + score render above the fold, un-collapsed", async () => {
    mockedGetSession.mockResolvedValue(sessionFixture())
    renderPage()

    expect(await screen.findByText(/Fixed the auth middleware bug/)).toBeInTheDocument()
    const synthesis = screen.getByRole("region", { name: "Synthesis" })
    expect(within(synthesis).getByText("82/100")).toBeInTheDocument()
    expect(within(synthesis).getByText("B")).toBeInTheDocument()
  })

  it("collapses the full log (causal replay / replay / timeline) behind a closed details by default", async () => {
    mockedGetSession.mockResolvedValue(sessionFixture())
    renderPage()

    await screen.findByText(/Fixed the auth middleware bug/)
    const details = screen.getByText(/View full detail/).closest("details")
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute("open")

    const timeline = screen.getByLabelText("Timeline")
    expect(timeline).not.toBeVisible()
  })

  it("expanding the details summary reveals the timeline", async () => {
    mockedGetSession.mockResolvedValue(sessionFixture())
    renderPage()

    await screen.findByText(/Fixed the auth middleware bug/)
    const summary = screen.getByText(/View full detail/)
    summary.click()

    const timeline = screen.getByLabelText("Timeline")
    expect(timeline).toBeVisible()
  })

  it("keeps cost + integrity checks reachable", async () => {
    mockedGetSession.mockResolvedValue(sessionFixture())
    renderPage()

    await screen.findByText(/Fixed the auth middleware bug/)
    expect(screen.getByText(/api-equivalent/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Verify chain" })).toBeInTheDocument()
  })

  it("auto-expands the collapsed detail and scrolls to an #event- deep link", async () => {
    window.location.hash = "#event-evt_2"
    mockedGetSession.mockResolvedValue(sessionFixture())
    renderPage()

    await screen.findByText(/Fixed the auth middleware bug/)

    const details = screen.getByText(/View full detail/).closest("details")
    await waitFor(() => expect(details).toHaveAttribute("open"))

    const target = document.getElementById("event-evt_2")
    expect(target).not.toBeNull()
    await waitFor(() => expect(target).toHaveClass("motion-safe:animate-event-flash"))
  })

  it("shows a loading state while the session fetches", () => {
    mockedGetSession.mockReturnValue(new Promise(() => {}))
    renderPage()

    expect(screen.getByRole("status", { name: "Loading session" })).toBeInTheDocument()
  })

  it("surfaces an error state when the fetch fails", async () => {
    mockedGetSession.mockRejectedValue(new Error("network down"))
    renderPage()

    expect(await screen.findByRole("alert")).toHaveTextContent("network down")
  })
})
