import { describe, expect, it, afterEach } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { getSelectedOrgId, setSelectedOrgId, useSelectedOrgId } from "./org-scope"

const STORAGE_KEY = "yoru.selectedOrgId"

// The org-scope listener reacts purely to the StorageEvent payload (`e.key` /
// `e.newValue`), never re-reading localStorage itself — so dispatching the
// event is enough to simulate what a sibling tab's write delivers here. This
// also sidesteps this jsdom environment not implementing `window.localStorage`.
function dispatchStorage(newValue: string | null) {
  window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY, newValue }))
}

afterEach(() => {
  setSelectedOrgId(null)
})

describe("org-scope cross-tab sync", () => {
  it("a storage event from another tab updates the synchronous getter", () => {
    dispatchStorage("org_from_other_tab")
    expect(getSelectedOrgId()).toBe("org_from_other_tab")
  })

  it("a storage event re-renders components subscribed via useSelectedOrgId", () => {
    const { result } = renderHook(() => useSelectedOrgId())
    expect(result.current).toBeNull()

    act(() => dispatchStorage("org_42"))
    expect(result.current).toBe("org_42")

    act(() => dispatchStorage(null))
    expect(result.current).toBeNull()
  })

  it("ignores storage events for unrelated keys", () => {
    setSelectedOrgId("org_mine")
    act(() => {
      window.dispatchEvent(
        new StorageEvent("storage", { key: "unrelated.key", newValue: "org_other" }),
      )
    })
    expect(getSelectedOrgId()).toBe("org_mine")
  })
})
