import { useEffect, useRef } from "react"
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query"
import { listActivity } from "../lib/api"
import { useFilters } from "../features/sessions/filters"
import { FilterBar } from "../features/sessions/FilterBar"
import { ActivityRow } from "../features/sessions/ActivityRow"
import { EmptySessionsState } from "../features/sessions/EmptySessionsState"
import { Skeleton } from "../components/ui/Skeleton"
import type { ActivityList } from "../types/receipt"

const PAGE = 40

// Group-scoped activity feed: what agents DID, action by action, newest first.
// Reuses GET /activity, which the backend scopes to own + group-mates (same
// visible_emails_sync wall as the sessions list; cross-group wall). Authed
// dashboard only — never the public /s/:id surface.
export function FeedPage() {
  const filters = useFilters()
  const queryClient = useQueryClient()

  const query = useInfiniteQuery<ActivityList>({
    queryKey: ["activity", filters],
    queryFn: ({ pageParam }) =>
      listActivity({ ...filters, limit: PAGE, offset: pageParam as number }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) =>
      // A full page implies there may be more; a short page is the end.
      lastPage.items.length === PAGE
        ? pages.reduce((n, p) => n + p.items.length, 0)
        : undefined,
  })

  const items = query.data?.pages.flatMap((p) => p.items) ?? []

  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el) return
    const io = new IntersectionObserver((entries) => {
      if (
        entries[0]?.isIntersecting &&
        query.hasNextPage &&
        !query.isFetchingNextPage
      ) {
        query.fetchNextPage()
      }
    })
    io.observe(el)
    return () => io.disconnect()
  }, [query.hasNextPage, query.isFetchingNextPage, query.fetchNextPage])

  return (
    <div className="space-y-4">
      <header className="border-b border-dashed border-rule pb-4">
        <h1 className="font-mono text-2xl font-semibold text-ink">Activity</h1>
        <p className="mt-1 font-mono text-caption text-ink-muted">
          What your agents are doing — tool calls, file edits, and red flags,
          newest first.
        </p>
      </header>

      <FilterBar />

      {query.isPending ? (
        <div role="status" aria-label="Loading activity" className="space-y-1">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton.ListRow key={i} decorative />
          ))}
        </div>
      ) : query.isError ? (
        <ErrorBanner
          message={
            query.error instanceof Error
              ? query.error.message
              : "Failed to load activity."
          }
          onRetry={() =>
            queryClient.invalidateQueries({ queryKey: ["activity"] })
          }
        />
      ) : items.length === 0 ? (
        <EmptySessionsState />
      ) : (
        <>
          <ul className="divide-y divide-dashed divide-rule">
            {items.map((a) => (
              <li key={a.id}>
                <ActivityRow activity={a} />
              </li>
            ))}
          </ul>
          <div ref={sentinel} aria-hidden className="h-8" />
          {query.isFetchingNextPage && (
            <div role="status" aria-label="Loading more" className="space-y-1">
              <Skeleton.ListRow decorative />
            </div>
          )}
          {!query.hasNextPage && (
            <p className="py-4 text-center font-mono text-micro text-ink-faint">
              — caught up —
            </p>
          )}
        </>
      )}
    </div>
  )
}

function ErrorBanner({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <div
      role="alert"
      className="rounded-sm border border-rule border-l-2 border-l-flag-env bg-surface p-4"
    >
      <p className="font-mono text-caption text-ink">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 rounded-sm border border-rule px-3 py-1 font-mono text-micro text-ink hover:bg-sunken"
      >
        Retry
      </button>
    </div>
  )
}
