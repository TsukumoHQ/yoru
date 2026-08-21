import { memo, useState } from "react"
import type { EventType, SessionEvent, CustomRuleInfo } from "./types"
import { RedFlagBadge, isCustomFlag } from "./RedFlagBadge"
import { ExpandedBody } from "./ExpandedBody"

interface TimelineEventProps {
  event: SessionEvent
  /** When true, render with a left rule + indent — used inside <TimelineGroup.Details>. */
  nested?: boolean
  onFlagClick?: () => void
  /** Controlled expansion from Timeline — falls back to local state if absent. */
  expanded?: boolean
  onToggle?: () => void
  /** Name/severity lookup for `custom:<uuid>` flags, keyed by the full flag
   *  string. Absent or missing entries fall back to RedFlagBadge's own
   *  short-id label — a custom hit is never dropped even pre-fetch. */
  customRuleInfo?: Record<string, CustomRuleInfo>
}

// Kind tag: `[tool]` / `[file]` / `[err]` / `[msg]` colored per category.
// Replaces the earlier glyph+label pair so the timeline reads like the
// marketing SampleReceipt (vault/FEEDBACK-2026-04-21.md §1).
const KIND_TAG: Record<EventType, string> = {
  tool_call:   "[tool]",
  file_change: "[file]",
  error:       "[err] ",
  message:     "[msg] ",
}

const KIND_TAG_CLASS: Record<EventType, string> = {
  tool_call:   "text-accent-500",
  file_change: "text-ink-muted",
  error:       "text-flag-env-fg",
  message:     "text-ink-faint",
}

// Message sub-types — backend puts the source in `event.tool` for kind=message
// so the single `message` EventType can fan out into `[you]` / `[note]` /
// `[sub]` without a new enum. Distinct tags differentiate user voice from
// system notices in the timeline at a glance.
const MSG_SUBTYPE_TAG: Record<string, string> = {
  user:         "[you] ",
  assistant:    "[bot] ",
  thinking:     "[thk] ",
  notification: "[note]",
  subagent:     "[sub] ",
}

const MSG_SUBTYPE_CLASS: Record<string, string> = {
  // You: readable on dark paper, semantic "this is me speaking" (ink, bold-ish).
  user:         "text-ink",
  // Claude's response: accent so the conversation flow reads distinctly.
  assistant:    "text-accent-500",
  // Thinking (extended-thinking blocks): muted — internal reasoning, not output.
  thinking:     "text-ink-muted",
  // Notification: system notice, low-key.
  notification: "text-ink-faint",
  // Subagent: lifecycle, even quieter.
  subagent:     "text-ink-faint",
}

function tagFor(event: SessionEvent): { text: string; className: string } {
  if (event.type === "message") {
    const sub = event.tool ?? event.tool_name
    if (sub && sub in MSG_SUBTYPE_TAG) {
      return { text: MSG_SUBTYPE_TAG[sub], className: MSG_SUBTYPE_CLASS[sub] }
    }
  }
  return { text: KIND_TAG[event.type], className: KIND_TAG_CLASS[event.type] }
}

// SR-only verb for assistive tech (tag is aria-hidden).
const VERB: Record<EventType, string> = {
  tool_call:   "ran",
  file_change: "wrote",
  error:       "errored",
  message:     "messaged",
}

// Row-tint mapping — left border + subtle bg per flag kind. Swiss palette:
// wine for secret, red for env, orange for shell, amber for migration,
// honey for ci, purple for db. Bg at 10% opacity so text stays readable.
function tintForFlag(flag?: string): string | null {
  switch (flag) {
    case "secret-pattern":
      return "border-l-2 border-l-flag-secret bg-flag-secret-bg/20"
    case "env-mutation":
      return "border-l-2 border-l-flag-env bg-flag-env-bg/20"
    case "shell-destructive":
      return "border-l-2 border-l-flag-shell bg-flag-shell-bg/20"
    case "db-destructive":
      return "border-l-2 border-l-flag-db bg-flag-db-bg/20"
    case "migration-edit":
      return "border-l-2 border-l-flag-migration bg-flag-migration-bg/20"
    case "ci-config-edit":
      return "border-l-2 border-l-flag-ci bg-flag-ci-bg/20"
    default:
      if (flag?.startsWith("custom:")) {
        return "border-l-2 border-l-flag-custom bg-flag-custom-bg/20"
      }
      return null
  }
}

function formatTimeOfDay(iso: string): string {
  // Local time for humans — the audit-grade canonical UTC reference lives
  // in the Hero's ISO timestamp + each row's `title` attr (hover). Rows
  // themselves show local so the user reads "when did this happen for me"
  // not "when did this happen in UTC".
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return d.toLocaleTimeString([], { hour12: false })
}

function TimelineEventImpl({
  event,
  nested = false,
  onFlagClick,
  expanded: expandedProp,
  onToggle,
  customRuleInfo,
}: TimelineEventProps) {
  const [localOpen, setLocalOpen] = useState(false)
  const controlled = expandedProp !== undefined && onToggle !== undefined
  const open = controlled ? expandedProp : localOpen
  const expandable = hasExpandableContent(event)
  const toggle = () => {
    if (!expandable) return
    if (controlled) onToggle()
    else setLocalOpen((v) => !v)
  }

  const label = labelFor(event)
  const primary = primaryFor(event)
  const timeOfDay = formatTimeOfDay(event.at)
  const isFlag = !!event.flag
  // Flag-aware row tint. Left-border + faint bg so the row jumps out in a
  // long timeline but doesn't overpower the text. Colors come from the
  // flag palette so the tint matches the RedFlagBadge hue.
  const flagRowTint = tintForFlag(event.flag)

  return (
    <li
      id={`event-${event.id}`}
      className={
        "motion-safe:animate-feed-in list-none border-b border-dashed border-rule last:border-b-0 " +
        (nested ? "pl-6 border-l border-rule " : "") +
        (flagRowTint ? flagRowTint + " " : "")
      }
    >
      <div
        role={expandable ? "button" : undefined}
        tabIndex={expandable ? 0 : undefined}
        aria-expanded={expandable ? open : undefined}
        onClick={toggle}
        onKeyDown={(e) => {
          if (!expandable) return
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault()
            toggle()
          }
        }}
        className={
          "group flex items-center gap-2 min-h-8 py-1 " +
          "hover:bg-sunken/40 focus-within:bg-sunken/40 " +
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper rounded-sm " +
          (expandable ? "cursor-pointer" : "")
        }
      >
        <span
          aria-hidden="true"
          className={`shrink-0 w-14 font-mono text-caption ${tagFor(event).className}`}
        >
          {tagFor(event).text}
        </span>
        <span className="sr-only">{VERB[event.type]}</span>
        <span className="flex-1 min-w-0 flex items-baseline gap-2 truncate" title={primary}>
          {showBoldLabel(event) && (
            <span className="shrink-0 font-mono text-sm font-semibold text-ink">
              {label}
            </span>
          )}
          <span
            className={
              "min-w-0 truncate font-mono text-sm group-hover:text-ink " +
              (event.type === "error"
                ? "text-flag-env-fg"
                : event.type === "message"
                  ? "font-sans italic text-ink-muted"
                  : "text-ink-muted")
            }
          >
            {primary || <span className="text-ink-faint">(no payload)</span>}
          </span>
        </span>
        {isFlag && event.flag && (
          <span className="shrink-0">
            <RedFlagBadge
              kind={event.flag}
              onClick={onFlagClick}
              label={isCustomFlag(event.flag) ? customRuleInfo?.[event.flag]?.name : undefined}
              severity={isCustomFlag(event.flag) ? customRuleInfo?.[event.flag]?.severity : undefined}
            />
          </span>
        )}
        {/* Duration chip — only for events >= 1s. Quick perf signal: slow
            Bash / Read / WebFetch jumps out. */}
        {typeof event.duration_ms === "number" && event.duration_ms >= 1000 && (
          <span
            className="shrink-0 font-mono text-micro tabular-nums text-accent-500 whitespace-nowrap"
            title={`${event.duration_ms}ms`}
          >
            {formatDurationMs(event.duration_ms)}
          </span>
        )}
        <time
          className="shrink-0 font-mono text-micro text-ink-faint tabular-nums whitespace-nowrap"
          dateTime={event.at}
          title={event.at}
        >
          {timeOfDay}
        </time>
        {expandable && (
          <span
            aria-hidden="true"
            className="shrink-0 w-4 text-center font-mono text-micro text-ink-faint group-hover:text-ink"
          >
            {open ? "−" : "+"}
          </span>
        )}
      </div>
      {expandable && open && (
        <div className="mt-1 mb-2 border-l-2 border-accent-500/40 pl-3">
          <ExpandedBody event={event} />
        </div>
      )}
    </li>
  )
}

export const TimelineEvent = memo(TimelineEventImpl)

// ── Grammar helpers ─────────────────────────────────────────────────────────

function labelFor(event: SessionEvent): string {
  switch (event.type) {
    case "tool_call":
      return event.tool ?? event.tool_name ?? "tool"
    case "file_change":
      return event.file_op ?? "edit"
    case "error":
      return "err"
    case "message":
      return "msg"
  }
}

// Only tool_call + file_change get a bold "what" token (tool name / op) in
// front of the primary content. For error/message the [err]/[msg] chip already
// classifies; a bold "err"/"msg" label is redundant and noisy.
function showBoldLabel(event: SessionEvent): boolean {
  return event.type === "tool_call" || event.type === "file_change"
}

function primaryFor(event: SessionEvent): string {
  switch (event.type) {
    case "tool_call": {
      // Prefer explicit content (e.g. Bash command, Grep pattern); fall back to a
      // best-effort one-liner from tool_input. Truncation handled by CSS .truncate.
      if (event.content && event.content.trim()) return collapseWhitespace(event.content)
      if (event.path) return event.path
      return toolInputOneLiner(event.tool_input) || ""
    }
    case "file_change":
      return event.path ?? event.file_path ?? "(unknown path)"
    case "error":
      return event.error_message ?? "Error"
    case "message": {
      // First non-empty line only — long pasted messages would otherwise smash
      // newlines into a wall of text in the primary row. Full body is still
      // visible in the expanded view via MessageBody (whitespace-pre-wrap).
      const txt = event.text ?? event.content ?? ""
      const firstLine = txt.split("\n").find((l) => l.trim().length > 0) ?? ""
      return firstLine.trim()
    }
  }
}

function hasExpandableContent(event: SessionEvent): boolean {
  if (event.content && event.content.length > 0) return true
  if (event.tool_input !== undefined && event.tool_input !== null) return true
  if (event.type === "error" && event.error_message) return true
  if (event.type === "message" && event.text && event.text.length > 80) return true
  if (event.output && event.output.length > 0) return true
  return false
}

export function formatDurationMs(ms: number | undefined): string {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return ""
  if (ms < 1) return ""
  if (ms < 1000) return `${Math.round(ms)}ms`
  const s = ms / 1000
  if (s < 10) return `${s.toFixed(1)}s`
  return `${Math.round(s)}s`
}

function collapseWhitespace(s: string): string {
  return s.replace(/\s+/g, " ").trim()
}

function toolInputOneLiner(input: unknown): string {
  if (input === undefined || input === null) return ""
  if (typeof input === "string") return collapseWhitespace(input)
  try {
    const obj = input as Record<string, unknown>
    // Best-effort priority order — most tool schemas surface one of these.
    const candidates = ["command", "pattern", "file_path", "path", "query", "url"]
    for (const k of candidates) {
      const v = obj[k]
      if (typeof v === "string" && v.length > 0) return collapseWhitespace(v)
    }
    return collapseWhitespace(JSON.stringify(input))
  } catch {
    return ""
  }
}

