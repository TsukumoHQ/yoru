import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { UsagePage, detectSpike } from "./UsagePage"
import { apiFetch, getTokenAnalytics } from "../lib/api"
import type { TokenAnalyticsBucket, TokenAnalyticsOut } from "../lib/api"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    apiFetch: vi.fn(),
    getTokenAnalytics: vi.fn(),
  }
})

const mockedApiFetch = vi.mocked(apiFetch)
const mockedGetTokenAnalytics = vi.mocked(getTokenAnalytics)

function bucket(iso: string, cost: number): TokenAnalyticsBucket {
  return { bucket_start: iso, tokens_input: 1000, tokens_output: 500, cost_usd: cost }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <UsagePage />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  mockedApiFetch.mockReset()
  mockedGetTokenAnalytics.mockReset()
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
    expect(screen.getByText(/\$7\.00 total/)).toBeInTheDocument()
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

  it("surfaces top spenders when org scope is present", async () => {
    mockedApiFetch.mockReset()
    mockedApiFetch.mockResolvedValueOnce({
      items: [{ id: "org_1", name: "Acme", slug: "acme", type: "team" }],
    })
    const ownOnly: TokenAnalyticsOut = {
      bucket: "day",
      since: "2026-08-14T00:00:00Z",
      until: "2026-08-21T00:00:00Z",
      own: {
        totals: { tokens_input: 1000, tokens_output: 500, cost_usd: 1 },
        series: [bucket("2026-08-20", 1)],
      },
      org: null,
    }
    const withOrg: TokenAnalyticsOut = {
      ...ownOnly,
      org: {
        totals: { tokens_input: 5000, tokens_output: 2500, cost_usd: 12 },
        series: [bucket("2026-08-20", 12)],
        top_spenders: [
          { user: "alice@acme.dev", tokens_input: 3000, tokens_output: 1500, cost_usd: 8 },
        ],
        by_dev: [
          { user: "alice@acme.dev", tokens_input: 3000, tokens_output: 1500, cost_usd: 8 },
        ],
        by_project: [{ vcs: "github", tokens_input: 3000, tokens_output: 1500, cost_usd: 8 }],
      },
    }
    // own-scope call (no org_id) first, then the best-effort org-scoped overlay.
    mockedGetTokenAnalytics.mockResolvedValueOnce(ownOnly).mockResolvedValueOnce(withOrg)
    renderPage()

    expect(await screen.findByText("Top spenders")).toBeInTheDocument()
    expect(screen.getByText("alice@acme.dev")).toBeInTheDocument()
    expect(mockedGetTokenAnalytics).toHaveBeenLastCalledWith(
      expect.objectContaining({ orgId: "org_1" }),
    )
  })

  it("keeps own-scope usage visible when the org overlay 403s (non-admin member)", async () => {
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
        series: [bucket("2026-08-20", 3)],
      },
      org: null,
    }
    mockedGetTokenAnalytics
      .mockResolvedValueOnce(ownOnly)
      .mockRejectedValueOnce(new Error("API 403: not an org admin"))
    renderPage()

    expect(await screen.findByText(/\$3\.00 total/)).toBeInTheDocument()
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
