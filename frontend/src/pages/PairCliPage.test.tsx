import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { PairCliPage } from "./PairCliPage"
import { apiFetch, ApiError } from "../lib/api"

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>()
  return {
    ...actual,
    apiFetch: vi.fn(),
  }
})

const mockedApiFetch = vi.mocked(apiFetch)

function renderPage() {
  render(
    <MemoryRouter>
      <PairCliPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  mockedApiFetch.mockReset()
})

describe("PairCliPage", () => {
  it("renders the pairing form", () => {
    renderPage()
    expect(screen.getByRole("heading", { name: "yoru" })).toBeInTheDocument()
    expect(screen.getByLabelText("Pairing code")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /authorize this device/i })).toBeDisabled()
  })

  it("authorizes on a valid code and shows the success state", async () => {
    mockedApiFetch.mockResolvedValueOnce(undefined)
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText("Pairing code"), "ABCDEFGH")
    await user.click(screen.getByRole("button", { name: /authorize this device/i }))

    expect(await screen.findByText("device authorized")).toBeInTheDocument()
    expect(mockedApiFetch).toHaveBeenCalledWith(
      "/auth/device-code/approve",
      expect.objectContaining({ method: "POST" }),
    )
  })

  async function triggerError(err: unknown) {
    mockedApiFetch.mockRejectedValueOnce(err)
    renderPage()
    const user = userEvent.setup()
    await user.type(screen.getByLabelText("Pairing code"), "ABCDEFGH")
    await user.click(screen.getByRole("button", { name: /authorize this device/i }))
    return screen.findByRole("alert")
  }

  it("404 (unknown code) shows the invalid-code message, no expired hint", async () => {
    const alert = await triggerError(new ApiError(404, "unknown pairing code"))
    expect(alert).toHaveAttribute("data-error-reason", "invalid")
    expect(alert).toHaveTextContent("doesn't match a pending pairing")
    expect(alert).not.toHaveTextContent("may be expired")
  })

  it("410 (expired) shows the expired message AND the re-run hint", async () => {
    const alert = await triggerError(new ApiError(410, "code has expired"))
    expect(alert).toHaveAttribute("data-error-reason", "expired")
    expect(alert).toHaveTextContent("has expired")
    expect(alert).toHaveTextContent("Code may be expired. Re-run")
  })

  it("409 (already paired) shows the already-used message + a revoke link, no expired hint", async () => {
    const alert = await triggerError(new ApiError(409, "code is approved"))
    expect(alert).toHaveAttribute("data-error-reason", "already-paired")
    expect(alert).toHaveTextContent("already used to pair a device")
    expect(alert).not.toHaveTextContent("may be expired")
    expect(within(alert).getByRole("link", { name: /settings.*tokens/i })).toBeInTheDocument()
  })

  it("429 (rate-limited) shows the lockout message, no expired hint", async () => {
    const alert = await triggerError(new ApiError(429, "too many attempts"))
    expect(alert).toHaveAttribute("data-error-reason", "rate-limited")
    expect(alert).toHaveTextContent("Too many attempts")
    expect(alert).not.toHaveTextContent("may be expired")
  })

  it("an unclassified failure falls back to the raw message, no expired hint", async () => {
    const alert = await triggerError(new Error("Failed to fetch"))
    expect(alert).toHaveAttribute("data-error-reason", "unknown")
    expect(alert).toHaveTextContent("Failed to fetch")
    expect(alert).not.toHaveTextContent("may be expired")
  })

  it("never logs the pairing code or token to the console", async () => {
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {})
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {})
    await triggerError(new ApiError(409, "code is approved"))
    expect(logSpy).not.toHaveBeenCalled()
    expect(warnSpy).not.toHaveBeenCalled()
    logSpy.mockRestore()
    warnSpy.mockRestore()
  })
})
