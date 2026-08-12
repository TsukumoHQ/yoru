import { memo } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { agentSourceLabel } from "@receipt/ui"
import type { Session } from "../../types/receipt"
import { formatCost, formatDuration, formatRelative } from "../../lib/format"
import { RedFlagBadge } from "./RedFlagBadge"

interface SessionRowProps {
  session: Session
  gridCols: string
  /** Human-readable label for the session's workspace (if known). */
  workspaceName?: string | null
}

const cellCls = "px-3 py-2 min-w-0"

function SessionRowImpl({ session, gridCols, workspaceName }: SessionRowProps) {
  const running = session.ended_at === null
  const sourceLabel = agentSourceLabel(session.agent)
  return (
    <Link
      to={`/s/${session.id}`}
      aria-label={`Session by ${session.user_email}, started ${formatRelative(session.started_at)}`}
      className={
        "block hover:bg-sunken " +
        gridCols + " " +
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
        "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
      }
    >
      <div className={cellCls + " min-w-0"}>
        {session.title ? (
          <>
            <div className="truncate font-sans text-caption text-ink" title={session.title}>
              {session.title}
            </div>
            <div className="truncate font-mono text-micro text-ink-faint" title={session.user_email}>
              {session.user_email}
            </div>
          </>
        ) : (
          <div className="truncate font-mono text-caption text-ink" title={session.user_email}>
            {session.user_email}
          </div>
        )}
      </div>
      <div className={cellCls}>
        <WorkspaceBadge
          workspaceId={session.workspace_id ?? null}
          name={workspaceName ?? null}
        />
      </div>
      <div className={cellCls}>
        <span
          className="inline-flex max-w-full items-center rounded-sm border border-rule bg-sunken px-1.5 py-0.5 font-mono text-micro text-ink-muted"
          title={`Event stream source: ${sourceLabel}`}
        >
          <span className="truncate">{sourceLabel}</span>
        </span>
      </div>
      <div className={cellCls + " font-mono text-caption text-ink-muted"} title={session.started_at}>
        {formatRelative(session.started_at)}
      </div>
      <div className={cellCls + " font-mono text-caption tabular-nums"}>
        {running ? (
          <span className="italic text-accent-500">running…</span>
        ) : (
          <span className="text-ink-muted">{formatDuration(session.duration_ms)}</span>
        )}
      </div>
      <div className={cellCls + " font-mono text-caption tabular-nums text-ink-muted"}>
        {session.tool_count}
      </div>
      <div
        className={cellCls + " font-mono text-caption tabular-nums text-ink-muted"}
        title={`${(session.tokens_input ?? 0).toLocaleString()} in · ${(session.tokens_output ?? 0).toLocaleString()} out`}
      >
        {session.cost_usd ? formatCost(session.cost_usd) : "—"}
      </div>
      <div className={cellCls}>
        {session.flags.length === 0 ? (
          <span className="font-mono text-caption text-ink-faint">—</span>
        ) : (
          <div className="flex flex-wrap gap-1">
            {session.flags.map((flag) => (
              <RedFlagBadge key={flag} kind={flag} />
            ))}
          </div>
        )}
      </div>
    </Link>
  )
}

export const SessionRow = memo(SessionRowImpl)

/** Workspace badge that filters the sessions list on click. */
function WorkspaceBadge({
  workspaceId,
  name,
}: {
  workspaceId: string | null
  name: string | null
}) {
  const [params, setParams] = useSearchParams()
  const label = name ?? (workspaceId ? workspaceId.slice(0, 6) : "Personal")
  const active = params.get("workspace_id") === workspaceId
  const handleClick = (e: React.MouseEvent) => {
    // Don't navigate into the session detail — just filter.
    e.preventDefault()
    e.stopPropagation()
    const next = new URLSearchParams(params)
    if (active) next.delete("workspace_id")
    else if (workspaceId) next.set("workspace_id", workspaceId)
    setParams(next, { replace: true })
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      className={
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-micro transition-colors " +
        (active
          ? "border-ink bg-ink text-canvas"
          : "border-rule bg-sunken text-ink-muted hover:border-ink hover:text-ink")
      }
      title={workspaceId ? `Filter by ${label}` : "No workspace (fallback)"}
    >
      {label}
    </button>
  )
}
