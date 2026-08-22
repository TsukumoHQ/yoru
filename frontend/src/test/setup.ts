import "@testing-library/jest-dom/vitest"
import { afterEach } from "vitest"
import { cleanup } from "@testing-library/react"

// jsdom doesn't implement scrollIntoView (https://github.com/jsdom/jsdom/issues/1695) —
// any component that calls it (anchor auto-scroll, etc.) crashes with no error boundary.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {}
}

afterEach(() => {
  cleanup()
})
