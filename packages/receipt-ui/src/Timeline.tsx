import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react"
import { useSearchParams } from "react-router-dom"
import { Virtuoso } from "react-virtuoso"
import type { EventType, SessionEvent, CustomRuleInfo } from "./types"
import { EmptyState } from "./EmptyState"
import { TimelineEvent } from "./TimelineEvent"
import { TimelineGroup } from "./TimelineGroup"
import { TimelineMinuteMarker } from "./TimelineMinuteMarker"
import {
  EMPTY_FILTERS,
  TimelineFilterBar,
  filterEvents,
  type MessageSubtype,
  type TimelineFilters,
} from "./TimelineFilterBar"

// Threshold above which the timeline switches from a plain `<ol>` to a
// Virtuoso-virtualized window. Under this size, the classic render keeps
// sticky minute markers + feed-in animations; above it, perf wins over the
// marker stickiness (sticky-inside-virtualized is awkward, so it's dropped).
const VIRT_THRESHOLD = 100

// Pagination: how many nodes to show initially + how many to add per
// "load more" click. Keeps the first paint light on huge sessions while
// still letting the user drill into the full history on demand.
const PAGE_INITIAL = 100
const PAGE_STEP = 200

// ── URL <-> filters serialization ───────────────────────────────────────────
// Filters persist to `?type=&subtype=&q=&flags=` so a shared URL reopens the
// same filtered view. Unknown values → null. Empty query stripped.

const VALID_TYPES: EventType[] = ["tool_call", "file_change", "error", "message"]
const VALID_SUBTYPES: MessageSubtype[] = ["user", "assistant", "thinking", "notification", "subagent"]

function filtersFromParams(sp: URLSearchParams): TimelineFilters {
  const rawType = sp.get("type") as EventType | null
  const rawSub = sp.get("subtype") as MessageSubtype | null
  return {
    type: rawType && VALID_TYPES.includes(rawType) ? rawType : null,
    subtype: rawSub && VALID_SUBTYPES.includes(rawSub) ? rawSub : null,
    toolName: sp.get("tool") || null,
    query: sp.get("q") ?? "",
    flagsOnly: sp.get("flags") === "1",
  }
}

function paramsFromFilters(f: TimelineFilters): URLSearchParams {
  const sp = new URLSearchParams()
  if (f.type) sp.set("type", f.type)
  if (f.subtype) sp.set("subtype", f.subtype)
  if (f.toolName) sp.set("tool", f.toolName)
  if (f.query.trim()) sp.set("q", f.query.trim())
  if (f.flagsOnly) sp.set("flags", "1")
  return sp
}

interface TimelineProps {
  events: SessionEvent[]
  /** Forwarded to flagged TimelineEvent rows so the legend modal can open. */
  onFlagClick?: () => void
  /** Name/severity lookup for `custom:<uuid>` flags — forwarded to every
   *  flagged row so a custom hit shows its rule name, not just "custom". */
  customRuleInfo?: Record<string, CustomRuleInfo>
}

// Minimum size to coalesce consecutive same-tool events into a group.
// Two same-tool calls read fine as two rows; three+ start to wall-of-sames.
// (SESSION-DETAIL-V1.md §8 open question 1, committed value.)
const MIN_GROUP_SIZE = 3

type TimelineNode =
  | { kind: "marker"; key: string; ts: string }
  | { kind: "event"; key: string; event: SessionEvent }
  | { kind: "group"; key: string; tool: string; groupKey: string; events: SessionEvent[] }

export function Timeline({ events, onFlagClick, customRuleInfo }: TimelineProps) {
  const [searchParams, setSearchParams] = useSearchParams()
  const filters = useMemo(() => filtersFromParams(searchParams), [searchParams])
  const setFilters = useCallback(
    (next: TimelineFilters | ((prev: TimelineFilters) => TimelineFilters)) => {
      setSearchParams(
        (current) => {
          const currentFilters = filtersFromParams(current)
          const value = typeof next === "function" ? next(currentFilters) : next
          return paramsFromFilters(value)
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )
  const searchRef = useRef<HTMLInputElement>(null)

  // Expansion state lifted up so Virtuoso can unmount items off-screen
  // without losing which rows the user expanded. Keyed by event.id for
  // event rows and by groupKey for TimelineGroup summary rows.
  const [expandedEvents, setExpandedEvents] = useState<Set<string>>(() => new Set())
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set())
  const toggleEvent = useCallback((id: string) => {
    setExpandedEvents((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  const toggleGroup = useCallback((groupKey: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(groupKey)) next.delete(groupKey)
      else next.add(groupKey)
      return next
    })
  }, [])

  const filtered = useMemo(() => filterEvents(events, filters), [events, filters])
  // Newest first at the top — matches FileChanged + Red Flags panels and
  // makes pagination natural (cap the head, let the rest load on scroll).
  // Reversing the source list makes bucketByMinute + collapseRuns emit the
  // minutes newest-first AND the events within each minute newest-first,
  // so markers still lead their minute group at the top.
  const allNodes = useMemo(() => buildNodes(filtered.slice().reverse()), [filtered])

  // Pagination — reset the visible window whenever filters change (otherwise
  // a user who expanded to see 600 older events would still be "scrolled"
  // after switching to type=message).
  const [visibleCount, setVisibleCount] = useState<number>(PAGE_INITIAL)
  useEffect(() => {
    setVisibleCount(PAGE_INITIAL)
  }, [filters.type, filters.subtype, filters.toolName, filters.query, filters.flagsOnly])
  const nodes = useMemo(
    () => allNodes.slice(0, visibleCount),
    [allNodes, visibleCount],
  )
  const remaining = Math.max(0, allNodes.length - nodes.length)

  // Rail "jump to flagged event" — the rail dispatches `receipt:focus-event`
  // with the target id. We:
  //   1. expand that row's body so the user lands on the full payload
  //   2. auto-grow pagination so the row is in the rendered slice
  //   3. smooth-scroll + flash (same animation as the hash handler)
  useEffect(() => {
    function onFocus(ev: Event) {
      const id = (ev as CustomEvent<{ id: string }>).detail?.id
      if (!id) return
      // 1. expand
      setExpandedEvents((prev) => {
        if (prev.has(id)) return prev
        const next = new Set(prev)
        next.add(id)
        return next
      })
      // 2. grow window if needed — find the node's index in allNodes.
      const idx = allNodes.findIndex(
        (n) => n.kind === "event" && String(n.event.id) === id,
      )
      if (idx >= 0) {
        setVisibleCount((v) => (idx >= v ? idx + 1 : v))
      }
      // 3. scroll + flash on next frame (after the row is mounted).
      requestAnimationFrame(() => {
        const el = document.getElementById(`event-${id}`)
        if (!el) return
        el.scrollIntoView({ behavior: "smooth", block: "center" })
        el.classList.add("motion-safe:animate-event-flash")
        setTimeout(() => el.classList.remove("motion-safe:animate-event-flash"), 1600)
      })
    }
    window.addEventListener("receipt:focus-event", onFocus)
    return () => window.removeEventListener("receipt:focus-event", onFocus)
  }, [allNodes])

  // Keyboard shortcuts: `/` focus search, `f` toggle flags-only, `Esc` clear.
  // Skipped when typing in an input/textarea so users can search freely.
  useEffect(() => {
    if (events.length === 0) return
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null
      const tag = target?.tagName
      const inField =
        tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable
      if (e.key === "/" && !inField) {
        e.preventDefault()
        searchRef.current?.focus()
        searchRef.current?.select()
      } else if (e.key === "f" && !inField) {
        e.preventDefault()
        setFilters((f) => ({ ...f, flagsOnly: !f.flagsOnly }))
      } else if (e.key === "Escape") {
        if (inField && target === searchRef.current) {
          searchRef.current?.blur()
        }
        setFilters(EMPTY_FILTERS)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [events.length])

  if (events.length === 0) {
    return (
      <section aria-label="Timeline" className="px-4 py-6">
        <EmptyState
          heading="SESSION IN PROGRESS"
          body="No events recorded yet. Tool calls and file edits will appear here as they stream."
        />
      </section>
    )
  }

  return (
    <section aria-label="Timeline">
      <TimelineFilterBar
        ref={searchRef}
        events={events}
        filters={filters}
        onChange={setFilters}
        filteredCount={filtered.length}
        totalCount={events.length}
      />
      <div className="px-4 py-4">
        <h2 className="mb-2 flex items-baseline justify-between font-mono text-micro uppercase tracking-wider text-ink-faint">
          <span>
            § Timeline · showing {nodes.length} of {allNodes.length}
            {filtered.length < events.length && ` · filtered ${filtered.length}/${events.length}`}
          </span>
          <span className="text-ink-faint">
            <kbd className="rounded-sm border border-rule px-1">/</kbd> search
            <span className="mx-1">·</span>
            <kbd className="rounded-sm border border-rule px-1">f</kbd> flags
            <span className="mx-1">·</span>
            <kbd className="rounded-sm border border-rule px-1">esc</kbd> clear
          </span>
        </h2>
        {filtered.length === 0 ? (
          <EmptyState
            heading="NO MATCHES"
            body="No events match the current filters. Press Esc to clear."
          />
        ) : (
          <>
            {nodes.length > VIRT_THRESHOLD ? (
              <Virtuoso
                useWindowScroll
                data={nodes}
                computeItemKey={(_, node) => node.key}
                itemContent={(_, node) =>
                  renderNode(node, {
                    onFlagClick,
                    expandedEvents,
                    toggleEvent,
                    expandedGroups,
                    toggleGroup,
                    customRuleInfo,
                  })
                }
                overscan={600}
              />
            ) : (
              <ol>
                {nodes.map((node) =>
                  renderNode(node, {
                    onFlagClick,
                    expandedEvents,
                    toggleEvent,
                    expandedGroups,
                    toggleGroup,
                    customRuleInfo,
                  }),
                )}
              </ol>
            )}
            {remaining > 0 && (
              <PageMoreButton
                remaining={remaining}
                step={PAGE_STEP}
                onLoadMore={(n) => setVisibleCount((v) => v + n)}
                onLoadAll={() => setVisibleCount(allNodes.length)}
              />
            )}
          </>
        )}
      </div>
    </section>
  )
}

function PageMoreButton({
  remaining,
  step,
  onLoadMore,
  onLoadAll,
}: {
  remaining: number
  step: number
  onLoadMore: (n: number) => void
  onLoadAll: () => void
}) {
  const next = Math.min(step, remaining)
  return (
    <div className="flex items-center justify-between gap-2 border-t border-dashed border-rule px-4 py-3">
      <p className="font-mono text-caption text-ink-muted tabular-nums">
        {remaining} older event{remaining === 1 ? "" : "s"} hidden
      </p>
      <div className="flex shrink-0 gap-1.5">
        <button
          type="button"
          onClick={() => onLoadMore(next)}
          className={
            "rounded-sm border border-rule px-2 py-0.5 font-mono text-micro uppercase tracking-wider text-ink-muted " +
            "hover:bg-sunken hover:text-ink " +
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
            "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
          }
        >
          load {next} older
        </button>
        {remaining > step && (
          <button
            type="button"
            onClick={onLoadAll}
            className={
              "rounded-sm border border-rule px-2 py-0.5 font-mono text-micro uppercase tracking-wider text-ink-faint " +
              "hover:bg-sunken hover:text-ink-muted " +
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
              "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
            }
          >
            all
          </button>
        )}
      </div>
    </div>
  )
}

interface RenderCtx {
  onFlagClick?: () => void
  expandedEvents: Set<string>
  toggleEvent: (id: string) => void
  expandedGroups: Set<string>
  toggleGroup: (groupKey: string) => void
  customRuleInfo?: Record<string, CustomRuleInfo>
}

// Single renderer shared by the virtualized and non-virtualized branches.
// Passes controlled expansion state so Virtuoso remounts don't reset UI.
function renderNode(node: TimelineNode, ctx: RenderCtx): ReactElement {
  switch (node.kind) {
    case "marker":
      return <TimelineMinuteMarker key={node.key} ts={node.ts} />
    case "event": {
      const id = String(node.event.id)
      return (
        <TimelineEvent
          key={node.key}
          event={node.event}
          onFlagClick={ctx.onFlagClick}
          expanded={ctx.expandedEvents.has(id)}
          onToggle={() => ctx.toggleEvent(id)}
          customRuleInfo={ctx.customRuleInfo}
        />
      )
    }
    case "group":
      return (
        <TimelineGroup
          key={node.key}
          groupKey={node.groupKey}
          tool={node.tool}
          events={node.events}
          expanded={ctx.expandedGroups.has(node.groupKey)}
          onToggle={() => ctx.toggleGroup(node.groupKey)}
          onFlagClick={ctx.onFlagClick}
          expandedEvents={ctx.expandedEvents}
          toggleEvent={ctx.toggleEvent}
          customRuleInfo={ctx.customRuleInfo}
        >
          <TimelineGroup.Summary />
          <TimelineGroup.Details />
        </TimelineGroup>
      )
  }
}

// ── Grouping passes ─────────────────────────────────────────────────────────
// Pass 1: bucket by minute (HH:MM, UTC). Minute boundary is a hard visual
//         separator — groups never cross it (SESSION-DETAIL-V1.md §8 q2).
// Pass 2: within each minute, walk events; coalesce consecutive runs sharing
//         `group_key` (or `tool_name` fallback) into TimelineGroup nodes if
//         the run length is ≥ MIN_GROUP_SIZE. Otherwise emit individual events.
// Pass 3: flatten into a node list with one marker per minute boundary first.

export function buildNodes(events: SessionEvent[]): TimelineNode[] {
  if (events.length === 0) return []
  const minutes = bucketByMinute(events)
  const out: TimelineNode[] = []
  for (const [minuteKey, bucket] of minutes) {
    out.push({ kind: "marker", key: `marker-${minuteKey}`, ts: bucket[0].at })
    const runs = collapseRuns(bucket)
    for (const run of runs) {
      if (run.events.length >= MIN_GROUP_SIZE && run.groupable) {
        // Two groups in different minute buckets (or re-entrant runs of
        // the same tool on the same path within a minute) can share a
        // groupKey — scope the React key to the minute + first event id
        // to guarantee uniqueness across the whole list.
        const firstId = run.events[0]?.id ?? ""
        out.push({
          kind: "group",
          key: `group-${minuteKey}-${firstId}-${run.groupKey}`,
          groupKey: run.groupKey,
          tool: run.tool,
          events: run.events,
        })
      } else {
        for (const event of run.events) {
          out.push({ kind: "event", key: `event-${event.id}`, event })
        }
      }
    }
  }
  return out
}

function bucketByMinute(events: SessionEvent[]): Map<string, SessionEvent[]> {
  const out = new Map<string, SessionEvent[]>()
  for (const e of events) {
    const key = minuteKey(e.at)
    const bucket = out.get(key)
    if (bucket) bucket.push(e)
    else out.set(key, [e])
  }
  return out
}

function minuteKey(at: string): string {
  const d = new Date(at)
  if (Number.isNaN(d.getTime())) return at
  // YYYY-MM-DDTHH:MM (UTC) — chronologically sortable AND unique per minute.
  return d.toISOString().slice(0, 16)
}

interface Run {
  tool: string
  groupKey: string
  events: SessionEvent[]
  groupable: boolean
}

function collapseRuns(events: SessionEvent[]): Run[] {
  const runs: Run[] = []
  for (const event of events) {
    const key = groupingKey(event)
    const tool = toolLabel(event)
    const groupable = groupableEvent(event)
    const last = runs[runs.length - 1]
    if (
      last &&
      last.groupable &&
      groupable &&
      key !== null &&
      last.groupKey === key &&
      last.tool === tool
    ) {
      last.events.push(event)
    } else {
      runs.push({
        tool,
        groupKey: key ?? `solo-${event.id}`,
        events: [event],
        groupable: groupable && key !== null,
      })
    }
  }
  return runs
}

function groupingKey(event: SessionEvent): string | null {
  if (event.group_key) return event.group_key
  if (event.type === "tool_call") {
    const tool = event.tool ?? event.tool_name
    if (tool) return `tool:${tool}:${minuteKey(event.at)}`
  }
  return null
}

function toolLabel(event: SessionEvent): string {
  if (event.type === "tool_call") return event.tool ?? event.tool_name ?? "tool"
  if (event.type === "file_change") return event.file_op ?? "edit"
  if (event.type === "error") return "err"
  return "msg"
}

function groupableEvent(event: SessionEvent): boolean {
  // Flag rows are never grouped (per §3 — flag rows are always expanded).
  if (event.flag) return false
  // Errors and messages are individually meaningful — never collapse.
  if (event.type === "error" || event.type === "message") return false
  return true
}
