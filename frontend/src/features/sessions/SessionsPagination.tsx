interface SessionsPaginationProps {
  page: number
  pageCount: number
  total: number
  onPage: (page: number) => void
}

const btnCls =
  "h-8 rounded-sm border border-rule px-3 font-mono text-micro uppercase tracking-wider " +
  "text-ink-muted hover:bg-sunken disabled:cursor-not-allowed disabled:opacity-50 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"

export function SessionsPagination({ page, pageCount, total, onPage }: SessionsPaginationProps) {
  return (
    <nav
      aria-label="Sessions pagination"
      className="flex items-center justify-between gap-3 font-mono text-micro text-ink-muted"
    >
      <span className="tabular-nums">
        Page {page} of {pageCount} · {total} sessions
      </span>
      <div className="flex gap-2">
        <button type="button" className={btnCls} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Prev
        </button>
        <button
          type="button"
          className={btnCls}
          disabled={page >= pageCount}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </div>
    </nav>
  )
}
