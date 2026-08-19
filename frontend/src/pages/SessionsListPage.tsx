import { useMemo } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { listSessions, listWorkspaces, type Workspace } from "../lib/api"
import { useSelectedOrgId } from "../lib/org-scope"
import { FilterBar } from "../features/sessions/FilterBar"
import { FleetStats } from "../features/sessions/FleetStats"
import { useFilters } from "../features/sessions/filters"
import { SessionsTable } from "../features/sessions/SessionsTable"
import { Skeleton } from "../components/ui/Skeleton"
import type { SessionList } from "../types/receipt"

export function SessionsListPage() {
  const filters = useFilters()
  const orgId = useSelectedOrgId()
  const queryClient = useQueryClient()

  // orgId is part of the key so a super-admin org switch refetches; the id is
  // forwarded as X-Organization-Id inside listSessions (lib/api orgHeaders).
  const query = useQuery<SessionList>({
    queryKey: ["sessions", filters, orgId],
    queryFn: () => listSessions(filters),
  })

  const workspacesQuery = useQuery<Workspace[]>({
    queryKey: ["me", "workspaces"],
    queryFn: listWorkspaces,
    staleTime: 60_000,
    meta: { silent: true },
  })

  const workspaceNameById = useMemo(() => {
    const m = new Map<string, string>()
    for (const w of workspacesQuery.data ?? []) m.set(w.id, w.name)
    return m
  }, [workspacesQuery.data])

  return (
    <div className="space-y-4">
      <header className="border-b border-dashed border-rule pb-4">
        <h1 className="font-mono text-2xl font-semibold text-ink">Receipts</h1>
      </header>

      {query.data && <FleetStats list={query.data} />}

      <FilterBar />

      {query.isPending ? (
        <div role="status" aria-label="Loading sessions" className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton.ListRow key={i} decorative />
          ))}
        </div>
      ) : query.isError ? (
        <ErrorBanner
          message={query.error instanceof Error ? query.error.message : "Failed to load sessions."}
          onRetry={() => queryClient.invalidateQueries({ queryKey: ["sessions"] })}
        />
      ) : (
        <SessionsTable
          sessions={query.data.items}
          workspaceNameById={workspaceNameById}
        />
      )}
    </div>
  )
}

function ErrorBanner({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start justify-between gap-3 rounded-sm border border-rule border-l-2 border-l-flag-env bg-surface px-3 py-2 text-sm text-ink"
    >
      <p className="flex-1">
        <span className="mr-2 font-mono text-ink-muted">[ERR]</span>
        <span>Couldn't load sessions. {message}</span>
      </p>
      <button
        type="button"
        onClick={onRetry}
        className={
          "shrink-0 rounded-sm border border-rule px-2 py-1 " +
          "font-mono text-micro uppercase tracking-wider text-ink-muted " +
          "hover:bg-sunken " +
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
          "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
        }
      >
        Retry
      </button>
    </div>
  )
}
