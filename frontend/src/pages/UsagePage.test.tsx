import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { UsagePage, detectSpike, periodDelta } from "./UsagePage"
import { apiFetch, getTokenAnalytics, getTokenAnalyticsSessions } from "../lib/api"
import type { TokenAnalyticsBucket, TokenAnalyticsOut, TokenAnalyticsSessionsOut } from "../lib/api"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    apiFetch: vi.fn(),
    getTokenAnalytics: vi.fn(),
    getTokenAnalyticsSessions: vi.fn(),
  }
})

const mockedApiFetch = vi.mocked(apiFetch)
const mockedGetTokenAnalytics = vi.mocked(getTokenAnalytics)
const mockedGetTokenAnalyticsSessions = vi.mocked(getTokenAnalyticsSessions)

function bucket(iso: string, cost: number): TokenAnalyticsBucket {
  return { bucket_start: iso, tokens_input: 1000, tokens_output: 500, cost_usd: cost }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <UsagePage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedApiFetch.mockReset()
  mockedGetTokenAnalytics.mockReset()
  mockedGetTokenAnalyticsSessions.mockReset()
  mockedApiFetch.mockResolvedValue({ items: [] })
})

describe("detectSpike", () => {
  it("flags the latest bucket when it is well above the trailing average", () => {
    const series = [bucket("2026-08-18", 1), bucket("2026-08-19", 1), bucket("2026-08-20", 5)]
    const spike = detectSpike(series)
    expect(spike).not.toBeNull()
    expect(spike?.ratio).toBeCloseTo(5, 1)
  })

  it("returns null when the latest bucket is in line with trailing spend", () => {
    const series = [bucket("2026-08-18", 1), bucket("2026-08-19", 1.1), bucket("2026-08-20", 1.05)]
    expect(detectSpike(series)).toBeNull()
  })

  it("returns null with fewer than 2 buckets or a zero trailing average", () => {
    expect(detectSpike([bucket("2026-08-20", 5)])).toBeNull()
    expect(detectSpike([bucket("2026-08-18", 0), bucket("2026-08-19", 5)])).toBeNull()
  })
})

describe("periodDelta", () => {
  it("flags an upward trend when the trailing half costs more than the leading half", () => {
    const series = [bucket("2026-08-18", 1), bucket("2026-08-19", 1), bucket("2026-08-20", 3), bucket("2026-08-21", 3)]
    const delta = periodDelta(series)
    expect(delta?.direction).toBe("up")
    expect(delta?.pct).toBeGreaterThan(0)
  })

  it("flags a downward trend when the trailing half costs less", () => {
    const series = [bucket("2026-08-18", 4), bucket("2026-08-19", 4), bucket("2026-08-20", 1), bucket("2026-08-21", 1)]
    const delta = periodDelta(series)
    expect(delta?.direction).toBe("down")
    expect(delta?.pct).toBeLessThan(0)
  })

  it("returns null for fewer than 2 buckets", () => {
    expect(periodDelta([bucket("2026-08-20", 5)])).toBeNull()
  })
})

describe("UsagePage", () => {
  it("renders the exception-first breakdown and highlights a spike", async () => {
    const data: TokenAnalyticsOut = {
      bucket: "day",
      since: "2026-08-14T00:00:00Z",
      until: "2026-08-21T00:00:00Z",
      own: {
        totals: { tokens_input: 4000, tokens_output: 2000, cost_usd: 7 },
        series: [bucket("2026-08-18", 1), bucket("2026-08-19", 1), bucket("2026-08-20", 5)],
      },
      org: null,
    }
    mockedGetTokenAnalytics.mockResolvedValueOnce(data)
    renderPage()

    expect(await screen.findByRole("alert")).toHaveTextContent(/Spike/)
    expect(screen.getByText("$7.00")).toBeInTheDocument()
  })

  it("shows the calm state when nothing is anomalous", async () => {
    const data: TokenAnalyticsOut = {
      bucket: "day",
      since: "2026-08-14T00:00:00Z",
      until: "2026-08-21T00:00:00Z",
      own: {
        totals: { tokens_input: 1000, tokens_output: 500, cost_usd: 1 },
        series: [bucket("2026-08-19", 1), bucket("2026-08-20", 1)],
      },
      org: null,
    }
    mockedGetTokenAnalytics.mockResolvedValueOnce(data)
    renderPage()

    expect(await screen.findByText(/No spend anomalies/)).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  function orgFixture() {
    const ownOnly: TokenAnalyticsOut = {
      bucket: "day",
      since: "2026-08-14T00:00:00Z",
      until: "2026-08-21T00:00:00Z",
      own: {
        totals: { tokens_input: 1000, tokens_output: 500, cost_usd: 1 },
        series: [bucket("2026-08-19", 0.45), bucket("2026-08-20", 0.55)],
      },
      org: null,
    }
    const withOrg: TokenAnalyticsOut = {
      ...ownOnly,
      org: {
        totals: { tokens_input: 5000, tokens_output: 2500, cost_usd: 12 },
        series: [bucket("2026-08-19", 4), bucket("2026-08-20", 8)],
        top_spenders: [
          { user: "alice@acme.dev", tokens_input: 3000, tokens_output: 1500, cost_usd: 8 },
        ],
        by_dev: [
          { user: "alice@acme.dev", tokens_input: 3000, tokens_output: 1500, cost_usd: 8 },
          { user: "bob@acme.dev", tokens_input: 2000, tokens_output: 1000, cost_usd: 4 },
        ],
        by_project: [
          { vcs: "github", tokens_input: 3000, tokens_output: 1500, cost_usd: 8 },
          { vcs: null, tokens_input: 2000, tokens_output: 1000, cost_usd: 4 },
        ],
      },
    }
    return { ownOnly, withOrg }
  }

  it("defaults to the org tab, org-first, with hero + dev roster + project breakdown", async () => {
    mockedApiFetch.mockReset()
    mockedApiFetch.mockResolvedValueOnce({
      items: [{ id: "org_1", name: "Acme", slug: "acme", type: "team" }],
    })
    const { ownOnly, withOrg } = orgFixture()
    mockedGetTokenAnalytics.mockResolvedValueOnce(ownOnly).mockResolvedValueOnce(withOrg)
    renderPage()

    expect(await screen.findByRole("tab", { name: "Org" })).toHaveAttribute("aria-selected", "true")
    expect(await screen.findByText("$12.00")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /alice@acme\.dev/ })).toBeInTheDocument()
    expect(screen.getByText("bob@acme.dev")).toBeInTheDocument()
    expect(screen.getByText("Github")).toBeInTheDocument()
    expect(screen.getByText("No repo")).toBeInTheDocument()
  })

  it("switches to the own tab and shows own totals", async () => {
    mockedApiFetch.mockReset()
    mockedApiFetch.mockResolvedValueOnce({
      items: [{ id: "org_1", name: "Acme", slug: "acme", type: "team" }],
    })
    const { ownOnly, withOrg } = orgFixture()
    mockedGetTokenAnalytics.mockResolvedValueOnce(ownOnly).mockResolvedValueOnce(withOrg)
    renderPage()

    await screen.findByText("$12.00")
    await userEvent.click(screen.getByRole("tab", { name: "You" }))

    expect(screen.getByRole("tab", { name: "You" })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByText("$1.00")).toBeInTheDocument()
  })

  it("drills a dev roster row into a session list", async () => {
    mockedApiFetch.mockReset()
    mockedApiFetch.mockResolvedValueOnce({
      items: [{ id: "org_1", name: "Acme", slug: "acme", type: "team" }],
    })
    const { ownOnly, withOrg } = orgFixture()
    mockedGetTokenAnalytics.mockResolvedValueOnce(ownOnly).mockResolvedValueOnce(withOrg)
    const sessions: TokenAnalyticsSessionsOut = {
      items: [
        {
          id: "sess_1",
          user: "alice@acme.dev",
          vcs: "github",
          started_at: "2026-08-20T10:00:00Z",
          tokens_input: 3000,
          tokens_output: 1500,
          cost_usd: 8,
        },
      ],
      total: 1,
    }
    mockedGetTokenAnalyticsSessions.mockResolvedValueOnce(sessions)
    renderPage()

    await screen.findByRole("button", { name: /alice@acme\.dev/ })
    await userEvent.click(screen.getByRole("button", { name: /alice@acme\.dev/ }))

    await waitFor(() => expect(mockedGetTokenAnalyticsSessions).toHaveBeenCalledWith(
      expect.objectContaining({ orgId: "org_1", dev: "alice@acme.dev" }),
    ))
    const links = await screen.findAllByRole("link")
    const sessionLink = links.find((l) => l.getAttribute("href") === "/s/sess_1")
    expect(sessionLink).toBeTruthy()
  })

  it("falls back to own usage with a message when the org overlay 403s (non-admin member)", async () => {
    mockedApiFetch.mockReset()
    mockedApiFetch.mockResolvedValueOnce({
      items: [{ id: "org_1", name: "Acme", slug: "acme", type: "team" }],
    })
    const ownOnly: TokenAnalyticsOut = {
      bucket: "day",
      since: "2026-08-14T00:00:00Z",
      until: "2026-08-21T00:00:00Z",
      own: {
        totals: { tokens_input: 1000, tokens_output: 500, cost_usd: 3 },
        series: [bucket("2026-08-19", 1.3), bucket("2026-08-20", 1.7)],
      },
      org: null,
    }
    mockedGetTokenAnalytics
      .mockResolvedValueOnce(ownOnly)
      .mockRejectedValueOnce(new Error("API 403: not an org admin"))
    renderPage()

    expect(await screen.findByText(/need org admin/)).toBeInTheDocument()
    expect(screen.getByText("$3.00")).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.getByText(/No spend anomalies/)).toBeInTheDocument()
  })

  it("shows an empty state when there is no usage at all", async () => {
    const data: TokenAnalyticsOut = {
      bucket: "day",
      since: "2026-08-14T00:00:00Z",
      until: "2026-08-21T00:00:00Z",
      own: { totals: { tokens_input: 0, tokens_output: 0, cost_usd: 0 }, series: [] },
      org: null,
    }
    mockedGetTokenAnalytics.mockResolvedValueOnce(data)
    renderPage()

    expect(await screen.findByText("No usage yet")).toBeInTheDocument()
  })

  it("surfaces an error banner with retry when the fetch fails", async () => {
    mockedGetTokenAnalytics.mockRejectedValueOnce(new Error("network down"))
    renderPage()

    expect(await screen.findByRole("alert")).toHaveTextContent("network down")
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
  })
})
