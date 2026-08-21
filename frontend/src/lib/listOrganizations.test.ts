import { describe, it, expect, vi, afterEach } from "vitest"
import { listOrganizations } from "./api"

function mockFetchOnce(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify(body),
    }),
  )
}

describe("listOrganizations", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("returns items for the normal {items:[...]} shape", async () => {
    mockFetchOnce({ items: [{ id: "o1", name: "Acme" }] })
    expect(await listOrganizations()).toEqual([{ id: "o1", name: "Acme" }])
  })

  it("returns [] for a bare array response", async () => {
    mockFetchOnce([{ id: "o1", name: "Acme" }])
    expect(await listOrganizations()).toEqual([{ id: "o1", name: "Acme" }])
  })

  it("returns [] for {} (no items key)", async () => {
    mockFetchOnce({})
    expect(await listOrganizations()).toEqual([])
  })

  it("returns [] for null", async () => {
    mockFetchOnce(null)
    expect(await listOrganizations()).toEqual([])
  })

  it("returns [] for a truthy non-array items shape ({organizations:[...]})", async () => {
    mockFetchOnce({ organizations: [{ id: "o1", name: "Acme" }] })
    expect(await listOrganizations()).toEqual([])
  })
})
