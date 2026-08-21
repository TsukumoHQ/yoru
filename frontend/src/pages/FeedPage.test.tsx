import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { FeedPage } from "./FeedPage"
import { listActivity } from "../lib/api"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    listActivity: vi.fn(),
  }
})

const mockedListActivity = vi.mocked(listActivity)

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
  it("renders the activity feed heading and filter bar", async () => {
    mockedListActivity.mockResolvedValueOnce({ items: [] })
    renderPage()

    expect(screen.getByRole("heading", { name: "Activity" })).toBeInTheDocument()
    expect(await screen.findByRole("status", { name: "No sessions yet" })).toBeInTheDocument()
  })

  it("surfaces an error banner with retry when the feed fails to load", async () => {
    mockedListActivity.mockRejectedValueOnce(new Error("network down"))
    renderPage()

    expect(await screen.findByRole("alert")).toHaveTextContent("network down")
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument()
  })
})
