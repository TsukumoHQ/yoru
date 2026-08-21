import { useSearchParams } from "react-router-dom"
import { EmptyState } from "@receipt/ui"

// Shown when the exception-first default (flag_only, DEC-yoru-product-principle-1)
// returns zero rows. Distinct from SessionsTableEmpty/EmptySessionsState (which
// claim "no sessions yet" — wrong here, since sessions may well exist, just none
// flagged) and from a plain filter-produced empty result.
export function NoExceptionsState() {
  const [params, setParams] = useSearchParams()

  const viewAll = () => {
    const next = new URLSearchParams(params)
    next.set("flag", "0")
    setParams(next)
  }

  return (
    <EmptyState
      heading="No exceptions right now"
      body="Nothing flagged — red flags and cost/token anomalies show up here the moment they happen."
      action={
        <button
          type="button"
          onClick={viewAll}
          className="rounded-sm border border-rule px-3 py-1.5 font-mono text-caption text-ink hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
        >
          View all activity →
        </button>
      }
    />
  )
}
