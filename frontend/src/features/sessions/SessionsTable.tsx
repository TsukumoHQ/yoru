import type { Session } from "../../types/receipt"
import { EmptyState } from "../../components/ui/EmptyState"
import { SessionRow } from "./SessionRow"

interface SessionsTableProps {
  sessions: Session[]
  workspaceNameById?: Map<string, string>
}

// Shared responsive grid template — every row (header + data) uses the same.
// Literal class strings so Tailwind JIT sees them.
// Phase W4: Workspace column between User and Started. Cost column added back
// (tokens/cost flow from the transcript tailer). Stream source column shows
// which CLI/plugin emitted the events.
const GRID_COLS =
  "sm:grid sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,0.5fr)_minmax(0,0.8fr)_minmax(0,1.5fr)]"

const headerCellCls =
  "px-3 py-2 text-left font-mono text-micro uppercase tracking-wider text-ink-faint"

export function SessionsTable({ sessions, workspaceNameById }: SessionsTableProps) {
  if (sessions.length === 0) return <SessionsTableEmpty />
  return (
    <div className="overflow-hidden rounded-sm border border-rule bg-surface">
      <div
        aria-hidden="true"
        className={"hidden " + GRID_COLS + " border-b border-rule bg-sunken/60"}
      >
        <div className={headerCellCls}>User</div>
        <div className={headerCellCls}>Workspace</div>
        <div className={headerCellCls}>Stream</div>
        <div className={headerCellCls}>Started</div>
        <div className={headerCellCls}>Duration</div>
        <div className={headerCellCls}>Tools</div>
        <div className={headerCellCls} title="api-equivalent — what this would cost at API per-token rates; a value-consumed reference, not your wallet">Cost</div>
        <div className={headerCellCls}>Flags</div>
      </div>
      <ul role="list" className="divide-y divide-rule">
        {sessions.map((s) => (
          <li key={s.id}>
            <SessionRow
              session={s}
              gridCols={GRID_COLS}
              workspaceName={
                s.workspace_id && workspaceNameById?.get(s.workspace_id)
              }
            />
          </li>
        ))}
      </ul>
    </div>
  )
}

export function SessionsTableEmpty() {
  return (
    <EmptyState
      heading="No sessions yet"
      body="Run `yoru init` in your terminal."
      action={
        <code className="rounded-sm bg-sunken px-2 py-1 font-mono text-caption text-ink">
          yoru init
        </code>
      }
    />
  )
}
