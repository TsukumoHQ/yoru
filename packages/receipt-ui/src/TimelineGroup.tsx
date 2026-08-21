import { createContext, memo, useContext, useMemo, useState, type ReactNode } from "react"
import type { SessionEvent, CustomRuleInfo } from "./types"
import { TimelineEvent, formatDurationMs } from "./TimelineEvent"

// Compound-component context per vercel-composition-patterns. Children read it
// via useTimelineGroup(); expansion state can either live locally (fallback
// for standalone use) or be controlled from above (Timeline lifts it so
// Virtuoso remounts don't drop expand state).
interface TimelineGroupContextValue {
  expanded: boolean
  toggle: () => void
  count: number
  tool: string
  totalDurationMs: number
  events: SessionEvent[]
  groupKey: string
  onFlagClick?: () => void
  expandedEvents?: Set<string>
  toggleEvent?: (id: string) => void
  customRuleInfo?: Record<string, CustomRuleInfo>
}

const TimelineGroupContext = createContext<TimelineGroupContextValue | null>(null)

function useTimelineGroup(): TimelineGroupContextValue {
  const ctx = useContext(TimelineGroupContext)
  if (!ctx) {
    throw new Error(
      "TimelineGroup.Summary / TimelineGroup.Details must be rendered inside <TimelineGroup>",
    )
  }
  return ctx
}

interface TimelineGroupProps {
  groupKey: string
  tool: string
  events: SessionEvent[]
  /** Controlled expansion from Timeline — if absent, falls back to local. */
  expanded?: boolean
  onToggle?: () => void
  onFlagClick?: () => void
  expandedEvents?: Set<string>
  toggleEvent?: (id: string) => void
  customRuleInfo?: Record<string, CustomRuleInfo>
  /** Always exactly <TimelineGroup.Summary /> + <TimelineGroup.Details />. */
  children: ReactNode
}

function TimelineGroupRoot({
  groupKey,
  tool,
  events,
  expanded: expandedProp,
  onToggle,
  onFlagClick,
  expandedEvents,
  toggleEvent,
  customRuleInfo,
  children,
}: TimelineGroupProps) {
  const controlled = expandedProp !== undefined && onToggle !== undefined
  const [localExpanded, setLocalExpanded] = useState(false)
  const expanded = controlled ? expandedProp : localExpanded
  const value = useMemo<TimelineGroupContextValue>(() => {
    const totalDurationMs = events.reduce((acc, e) => acc + (e.duration_ms ?? 0), 0)
    return {
      expanded,
      toggle: controlled ? onToggle! : () => setLocalExpanded((v) => !v),
      count: events.length,
      tool,
      totalDurationMs,
      events,
      groupKey,
      onFlagClick,
      expandedEvents,
      toggleEvent,
      customRuleInfo,
    }
  }, [expanded, controlled, onToggle, events, tool, groupKey, onFlagClick, expandedEvents, toggleEvent, customRuleInfo])
  return <TimelineGroupContext.Provider value={value}>{children}</TimelineGroupContext.Provider>
}

// ── Summary ─────────────────────────────────────────────────────────────────
// Single collapsed row:
//   ▶ Bash  ×N commands (preview, …)   total X.Ys   [expand]
// Click anywhere on the row toggles. Chevron rotates on expand.

function formatHHMMSS(iso: string): string {
  // Local time — same convention as TimelineEvent.
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleTimeString([], { hour12: false })
}

function SummaryImpl() {
  const { expanded, toggle, count, tool, totalDurationMs, events, groupKey } =
    useTimelineGroup()
  const previews = useMemo(() => firstNPreviews(events, 2), [events])
  const totalLabel = formatDurationMs(totalDurationMs)
  const firstTs = events[0]?.at ?? ""
  return (
    <li
      id={`group-${groupKey}`}
      className="motion-safe:animate-feed-in list-none border-b border-dashed border-rule last:border-b-0"
    >
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        aria-controls={`group-details-${groupKey}`}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            toggle()
          }
        }}
        className={
          "group flex items-center gap-2 min-h-8 py-1 cursor-pointer " +
          "hover:bg-sunken/40 focus-within:bg-sunken/40 " +
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper rounded-sm"
        }
      >
        <span
          aria-hidden="true"
          className="shrink-0 w-14 font-mono text-caption text-accent-500"
        >
          [tool]
        </span>
        <span className="flex-1 min-w-0 flex items-baseline gap-2 truncate">
          <span className="shrink-0 font-mono text-sm font-semibold text-ink">
            {tool}
          </span>
          <span className="shrink-0 font-mono text-caption text-ink-muted tabular-nums">
            ×{count}
          </span>
          <span
            className="min-w-0 truncate font-mono text-sm text-ink-muted group-hover:text-ink"
            title={previews.join(", ")}
          >
            {previews.length > 0 ? `(${previews.join(", ")} …)` : pluralize(count, tool)}
          </span>
        </span>
        {totalLabel && (
          <span className="shrink-0 font-mono text-micro text-ink-faint tabular-nums whitespace-nowrap">
            {totalLabel}
          </span>
        )}
        {firstTs && (
          <time
            className="shrink-0 font-mono text-micro text-ink-faint tabular-nums whitespace-nowrap"
            dateTime={firstTs}
            title={firstTs}
          >
            {formatHHMMSS(firstTs)}
          </time>
        )}
        <span
          aria-hidden="true"
          data-expanded={expanded}
          className="shrink-0 w-4 text-center font-mono text-micro text-ink-faint group-hover:text-ink transition-transform duration-micro data-[expanded=true]:rotate-90"
        >
          ›
        </span>
      </div>
    </li>
  )
}

const Summary = memo(SummaryImpl)

// ── Details ─────────────────────────────────────────────────────────────────
// Renders nothing when collapsed (mounted-only-when-expanded — saves a render
// pass for the 25-Bash collapsed-by-default case). When expanded, fans out the
// underlying events as nested TimelineEvent rows.

function DetailsImpl() {
  const { expanded, events, groupKey, onFlagClick, expandedEvents, toggleEvent, customRuleInfo } =
    useTimelineGroup()
  if (!expanded) return null
  return (
    <li id={`group-details-${groupKey}`} className="list-none">
      <ol className="ml-4">
        {events.map((event) => {
          const id = String(event.id)
          const controlled = expandedEvents && toggleEvent
          return (
            <TimelineEvent
              key={event.id}
              event={event}
              nested
              onFlagClick={onFlagClick}
              expanded={controlled ? expandedEvents.has(id) : undefined}
              onToggle={controlled ? () => toggleEvent(id) : undefined}
              customRuleInfo={customRuleInfo}
            />
          )
        })}
      </ol>
    </li>
  )
}

const Details = memo(DetailsImpl)

// Compound binding — `<TimelineGroup.Summary />` / `<TimelineGroup.Details />`.
// Root export is a memoized wrapper so consumers see one symbol.
export const TimelineGroup = Object.assign(memo(TimelineGroupRoot), {
  Summary,
  Details,
})

// ── Helpers ─────────────────────────────────────────────────────────────────

function firstNPreviews(events: SessionEvent[], n: number): string[] {
  const out: string[] = []
  for (const e of events) {
    if (out.length >= n) break
    const s = onelineForPreview(e)
    if (s) out.push(s)
  }
  return out
}

function onelineForPreview(event: SessionEvent): string {
  if (event.content) return truncate(collapseWhitespace(event.content), 40)
  if (event.path) return truncate(event.path, 40)
  if (typeof event.tool_input === "string") return truncate(collapseWhitespace(event.tool_input), 40)
  if (event.tool_input && typeof event.tool_input === "object") {
    const obj = event.tool_input as Record<string, unknown>
    for (const k of ["command", "pattern", "file_path", "path", "query"]) {
      const v = obj[k]
      if (typeof v === "string" && v.length > 0) return truncate(collapseWhitespace(v), 40)
    }
  }
  return ""
}

function collapseWhitespace(s: string): string {
  return s.replace(/\s+/g, " ").trim()
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s
}

function pluralize(n: number, tool: string): string {
  // Bash → "commands", Read/Edit/Grep → "calls". Conservative; not localized.
  if (tool.toLowerCase() === "bash") return n === 1 ? "command" : "commands"
  return n === 1 ? "call" : "calls"
}
