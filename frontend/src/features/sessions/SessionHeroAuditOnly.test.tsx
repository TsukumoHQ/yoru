import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import { SessionHeroView, type SessionDetail } from "@receipt/ui"

const BASE: SessionDetail = {
  id: "sess_1",
  user_email: "alice@acme.com",
  started_at: "2026-08-21T00:00:00Z",
  ended_at: "2026-08-21T00:05:00Z",
  duration_ms: 300_000,
  tool_count: 3,
  cost_usd: 0.02,
  tokens_input: 100,
  tokens_output: 50,
  flag_count: 0,
  flags: [],
  events: [],
  files_changed: [],
}

function renderHero(session: SessionDetail) {
  render(<SessionHeroView session={session} />)
}

describe("SessionHeroView / B3 audit-only badge", () => {
  it("shows the audit-only badge for an independent-capture session (enforce_available=false)", () => {
    renderHero({ ...BASE, agent_confidence: "unknown", enforce_available: false })

    expect(screen.getByText("audit-only · enforcement unavailable")).toBeInTheDocument()
  })

  it("shows the audit-only badge when the fields are absent (older contract defaults safe)", () => {
    renderHero({ ...BASE })

    expect(screen.getByText("audit-only · enforcement unavailable")).toBeInTheDocument()
  })

  it("hides the badge for a session with a declared adapter (enforce_available=true)", () => {
    renderHero({ ...BASE, agent_confidence: "declared", enforce_available: true })

    expect(screen.queryByText("audit-only · enforcement unavailable")).not.toBeInTheDocument()
  })

  it("does not turn the badge into an alert — it's a status chip, not an alarm", () => {
    renderHero({ ...BASE, agent_confidence: "unknown", enforce_available: false })

    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.getByText("audit-only · enforcement unavailable")).toHaveAttribute(
      "role",
      "status",
    )
  })
})
