import { formatDuration, formatRelative } from "./format"
import { RedFlagBadge } from "./RedFlagBadge"
import type { SessionDetail } from "./types"
import { agentSourceLabel } from "./agentSource"

interface SessionHeroViewProps {
  session: SessionDetail
  onExport?: () => void
  isExporting?: boolean
  onGenerateSummary?: () => void
  isGeneratingSummary?: boolean
  /** Issue #79 — share toggle. When provided, the hero renders a button
   *  that flips `is_public` via the parent's handler. Public-page renders
   *  of this component should not pass these props. */
  onToggleShare?: () => void
  isSharing?: boolean
  /** Public URL for the session when `session.is_public` is true —
   *  rendered as a copyable read-only field next to the toggle. */
  publicShareUrl?: string | null
  /** TSU-54 — local image export. When provided, the hero renders
   *  "Download PNG" / "Copy image" actions that fetch the self-contained
   *  receipt PNG (the share artifact is the image, not a hosted URL). */
  onDownloadReceipt?: () => void
  onCopyReceipt?: () => void
  isReceiptBusy?: boolean
  /** TSU-55 — download the animated session-replay GIF (local export). */
  onDownloadReplay?: () => void
  isReplayBusy?: boolean
}

const RUBRIC = "font-mono text-caption uppercase tracking-wider text-ink-faint"

export function SessionHeroView({
  session,
  onExport,
  isExporting = false,
  onGenerateSummary,
  isGeneratingSummary = false,
  onToggleShare,
  isSharing = false,
  publicShareUrl,
  onDownloadReceipt,
  onCopyReceipt,
  isReceiptBusy = false,
  onDownloadReplay,
  isReplayBusy = false,
}: SessionHeroViewProps) {
  const shortId = session.id.slice(0, 4)
  const sourceLabel = agentSourceLabel(session.agent)
  const summary = (session.summary ?? "").trim()
  // Token breakdown hidden pre-launch — see ROADMAP.md. Kept as a named
  // constant so re-enabling is a one-line render change.
  // const usage = session.usage_events ?? []
  // const newWork = ... (compute freshIn + cacheWrite + output from usage)

  return (
    <header className="rounded-sm border border-rule bg-surface">
      <div className="px-4 py-4 border-b border-dashed border-rule">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h1
            className="font-sans text-2xl font-semibold text-ink min-w-0 break-words"
            title={session.title ?? session.id}
          >
            {session.title ?? session.user_email}
          </h1>
          {(onExport || onToggleShare || onDownloadReceipt || onCopyReceipt || onDownloadReplay) && (
            <div className="ml-auto flex shrink-0 items-center gap-2">
              {onDownloadReplay && (
                <button
                  type="button"
                  onClick={onDownloadReplay}
                  disabled={isReplayBusy}
                  title="Download an animated replay of this session (GIF)"
                  className={
                    "inline-flex items-center gap-1 rounded-sm border border-rule px-2 py-1 " +
                    "font-mono text-caption uppercase tracking-wider text-ink-muted " +
                    "hover:bg-sunken hover:text-ink " +
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
                    "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
                    "disabled:opacity-60"
                  }
                >
                  {isReplayBusy ? "…" : "↓ gif"}
                </button>
              )}
              {onDownloadReceipt && (
                <button
                  type="button"
                  onClick={onDownloadReceipt}
                  disabled={isReceiptBusy}
                  title="Download a shareable receipt image (PNG)"
                  className={
                    "inline-flex items-center gap-1 rounded-sm border border-accent-500 px-2 py-1 " +
                    "font-mono text-caption uppercase tracking-wider text-accent-500 " +
                    "hover:bg-accent-500/10 " +
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
                    "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
                    "disabled:opacity-60"
                  }
                >
                  {isReceiptBusy ? "…" : "↓ png"}
                </button>
              )}
              {onCopyReceipt && (
                <button
                  type="button"
                  onClick={onCopyReceipt}
                  disabled={isReceiptBusy}
                  title="Copy the receipt image to your clipboard"
                  className={
                    "inline-flex items-center gap-1 rounded-sm border border-rule px-2 py-1 " +
                    "font-mono text-caption uppercase tracking-wider text-ink-muted " +
                    "hover:bg-sunken hover:text-ink " +
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
                    "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
                    "disabled:opacity-60"
                  }
                >
                  {isReceiptBusy ? "…" : "⧉ copy img"}
                </button>
              )}
              {onToggleShare && (
                <button
                  type="button"
                  onClick={onToggleShare}
                  disabled={isSharing}
                  title={
                    session.is_public
                      ? "This session is public — click to revoke"
                      : "Make this session publicly shareable"
                  }
                  aria-pressed={Boolean(session.is_public)}
                  className={
                    "inline-flex items-center gap-1 rounded-sm border px-2 py-1 " +
                    "font-mono text-caption uppercase tracking-wider " +
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
                    "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
                    "disabled:opacity-60 " +
                    (session.is_public
                      ? "border-accent-500 bg-accent-500/10 text-accent-500 hover:bg-accent-500/15"
                      : "border-rule text-ink-muted hover:bg-sunken hover:text-ink")
                  }
                >
                  {isSharing ? "…" : session.is_public ? "● public" : "○ share"}
                </button>
              )}
              {onExport && (
                <button
                  type="button"
                  onClick={onExport}
                  disabled={isExporting}
                  title="Download full audit trail (JSON)"
                  className={
                    "inline-flex items-center gap-1 rounded-sm border border-rule px-2 py-1 " +
                    "font-mono text-caption uppercase tracking-wider text-ink-muted " +
                    "hover:bg-sunken hover:text-ink " +
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
                    "focus-visible:ring-offset-2 focus-visible:ring-offset-paper " +
                    "disabled:opacity-60"
                  }
                >
                  {isExporting ? "…" : "↓ export trail"}
                </button>
              )}
            </div>
          )}
        </div>
        {session.is_public && publicShareUrl && (
          <p className="mt-3 flex flex-wrap items-center gap-2 font-mono text-caption text-ink-muted">
            <span className="text-ink-faint">public url:</span>
            <code className="select-all rounded-sm bg-sunken px-1.5 py-0.5 text-ink">
              {publicShareUrl}
            </code>
          </p>
        )}
        <p className={`${RUBRIC} mt-2 flex flex-wrap items-baseline gap-x-2`}>
          <span className="font-mono normal-case tracking-normal text-ink-muted">
            {session.user_email}
          </span>
          <span aria-hidden className="text-ink-faint">·</span>
          <span>session {shortId}</span>
          <span aria-hidden className="text-ink-faint">·</span>
          <span
            className="rounded-sm border border-rule bg-sunken px-1.5 py-0.5"
            title={`Event stream source: ${sourceLabel}`}
          >
            stream · {sourceLabel}
          </span>
          <span aria-hidden className="text-ink-faint">·</span>
          <time
            dateTime={session.started_at}
            title={session.started_at}
            className="tabular-nums"
          >
            {formatRelative(session.started_at)}
          </time>
        </p>
        {session.flags.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5">
            {session.flags.map((kind) => (
              <RedFlagBadge key={kind} kind={kind} />
            ))}
          </div>
        )}
      </div>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-4 py-3">
        <Stat label="duration">
          <span className="font-mono text-sm text-ink tabular-nums">
            {formatDuration(session.duration_ms)}
          </span>
        </Stat>
        <Stat label="tools">
          <span className="font-mono text-sm text-ink tabular-nums">{session.tool_count}</span>
        </Stat>
        {/* tokens and cost stats hidden pre-launch (see ROADMAP.md) —
            same decision as TokenPanel. Data keeps flowing from the
            backend; only the UI surface is hidden. */}
      </dl>

      <div className="border-t border-dashed border-rule px-4 py-3">
        <p className={`${RUBRIC} mb-1.5`}>§ Summary · Haiku 4.5</p>
        {summary ? (
          <p className="font-sans text-sm leading-relaxed text-ink">{summary}</p>
        ) : isGeneratingSummary ? (
          <div role="status" aria-label="Generating summary" className="space-y-2">
            <div aria-hidden className="h-4 w-3/4 rounded-sm bg-sunken/60 motion-safe:animate-pulse" />
            <div aria-hidden className="h-4 w-full rounded-sm bg-sunken/60 motion-safe:animate-pulse" />
            <div aria-hidden className="h-4 w-2/3 rounded-sm bg-sunken/60 motion-safe:animate-pulse" />
          </div>
        ) : onGenerateSummary ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="font-sans text-sm text-ink-muted">
              No summary generated yet. Haiku 4.5 will draft one from this session&apos;s events.
            </p>
            <button
              type="button"
              onClick={onGenerateSummary}
              className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-rule px-3 py-1 font-sans text-caption text-ink hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
            >
              Generate summary
            </button>
          </div>
        ) : (
          <p className="font-sans text-sm text-ink-muted">No summary yet.</p>
        )}
      </div>
    </header>
  )
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className={RUBRIC}>{label}</dt>
      <dd className="mt-1 min-w-0">{children}</dd>
    </div>
  )
}
