import { describe, expect, it } from "vitest"
import { parseFilters } from "./filters"

describe("parseFilters — exception-first default (DEC-yoru-product-principle-1)", () => {
  it("defaults flag_only to true with no params", () => {
    expect(parseFilters(new URLSearchParams()).flag_only).toBe(true)
  })

  it("stays true with unrelated params set", () => {
    expect(parseFilters(new URLSearchParams("user=alice@acme.dev")).flag_only).toBe(true)
  })

  it("flips to false only on explicit flag=0", () => {
    expect(parseFilters(new URLSearchParams("flag=0")).flag_only).toBe(false)
  })

  it("stays true on any other flag value", () => {
    expect(parseFilters(new URLSearchParams("flag=1")).flag_only).toBe(true)
  })
})
