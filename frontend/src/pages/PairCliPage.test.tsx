import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { PairCliPage } from "./PairCliPage"
import { apiFetch } from "../lib/api"

vi.mock("../lib/api", () => ({
  apiFetch: vi.fn(),
}))

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

  it("shows the error banner when the server rejects the code", async () => {
    mockedApiFetch.mockRejectedValueOnce(new Error("Code already used"))
    renderPage()
    const user = userEvent.setup()

    await user.type(screen.getByLabelText("Pairing code"), "ABCDEFGH")
    await user.click(screen.getByRole("button", { name: /authorize this device/i }))

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("Code already used")
    })
  })
})
