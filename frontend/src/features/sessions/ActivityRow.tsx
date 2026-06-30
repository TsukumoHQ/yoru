import { memo } from "react"
import { Link } from "react-router-dom"
import type { ActivityItem } from "../../types/receipt"
import { formatRelative } from "../../lib/format"
import { RedFlagBadge } from "./RedFlagBadge"

// A short verb for what the agent did, derived from kind + tool.
function action(a: ActivityItem): string {
  if (a.kind === "error") return "error"
  if (a.kind === "file_change") return a.tool === "Write" ? "created" : "edited"
  return a.tool ?? "ran" // tool_use
}

function ActivityRowImpl({ activity }: { activity: ActivityItem }) {
  const a = activity
  const isError = a.kind === "error"
  return (
    <Link
      to={`/s/${a.session_id}`}
      aria-label={`${a.user_email} · ${a.agent} — ${action(a)}${a.path ? " " + a.path : ""}, ${formatRelative(a.at)}`}
      className={
        "flex items-baseline gap-3 rounded-sm px-3 py-2 hover:bg-sunken " +
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
        "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
        (isError ? "border-l-2 border-l-red-500/60" : "border-l-2 border-l-transparent")
      }
    >
      <span
        className="shrink-0 font-mono text-micro tabular-nums text-ink-faint"
        title={a.at}
      >
        {formatRelative(a.at)}
      </span>
      <span className="shrink-0 truncate font-mono text-micro text-ink-muted" title={`${a.user_email} · ${a.agent}`}>
        {a.user_email}
        <span className="mx-1 text-rule">·</span>
        {a.agent}
      </span>
      <span className="min-w-0 flex-1 font-mono text-caption text-ink">
        <span className={isError ? "text-red-400" : "text-ink-muted"}>{action(a)}</span>
        {a.path && (
          <code className="ml-1.5 truncate text-ink" title={a.path}>
            {a.path}
          </code>
        )}
      </span>
      {a.flags.length > 0 && (
        <span className="flex shrink-0 flex-wrap items-center gap-1">
          {a.flags.map((k) => (
            <RedFlagBadge key={k} kind={k} />
          ))}
        </span>
      )}
    </Link>
  )
}

export const ActivityRow = memo(ActivityRowImpl)
