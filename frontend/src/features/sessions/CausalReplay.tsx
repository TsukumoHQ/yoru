// Causal-chain replay (steal #4) — the audit trail read the way a compliance
// officer reads it: the run as plain-English steps, plus a copyable run graph.
//
// It is a DERIVED view. The event store records an ordered stream, not an
// explicit causal graph, so the chain here is the recorded order (grouped by
// `group_key` where the agent tagged related events). The copy says "derived
// from the recorded order" out loud — no invented causality.
//
// Reads only the events already on the loaded session; no new API, no backend.
// Human-facing generated phrasing is kept plain and specific (anti-slop).
import { useCallback, useMemo, useState } from "react"
import { redactTokens, isCustomFlag } from "@receipt/ui"
import type { CustomRuleInfo, RedFlagKind, SessionEvent } from "../../types/receipt"

// Presets only — a custom:<uuid> flag is resolved via `customRuleInfo`
// (short-id fallback below) rather than this closed map.
const FLAG_LABEL: Partial<Record<RedFlagKind, string>> = {
  "secret-pattern": "secret",
  "shell-destructive": "destructive shell",
  "db-destructive": "destructive db",
  "migration-edit": "migration edit",
  "env-mutation": "env change",
  "ci-config-edit": "ci config edit",
}

interface Step {
  key: string
  text: string
  flags: RedFlagKind[]
  isError: boolean
}

function toolOf(e: SessionEvent): string | undefined {
  return e.tool_name ?? e.tool ?? undefined
}

function pathOf(e: SessionEvent): string | undefined {
  return e.file_path ?? e.path ?? undefined
}

function clip(s: string, n = 120): string {
  const one = s.replace(/\s+/g, " ").trim()
  return one.length > n ? one.slice(0, n - 1) + "…" : one
}

/** One plain-English sentence for an event. Specific, no filler. */
function describe(e: SessionEvent): string {
  const path = pathOf(e)
  switch (e.type) {
    case "file_change": {
      const verb =
        e.file_op === "create" ? "Created" : e.file_op === "delete" ? "Deleted" : "Edited"
      return path ? `${verb} ${path}` : `${verb} a file`
    }
    case "tool_call": {
      const tool = toolOf(e)
      if (tool && path) return `Ran the ${tool} tool on ${path}`
      if (tool) return `Ran the ${tool} tool`
      return "Ran a tool"
    }
    case "error":
      return e.error_message ? `Hit an error — ${clip(e.error_message)}` : "Hit an error"
    case "message":
      return "Exchanged a message with the model"
    default:
      return "Recorded an event"
  }
}

function buildSteps(events: SessionEvent[]): Step[] {
  return events.map((e, i) => ({
    key: e.id || String(i),
    // redactTokens is the same display-filter the message bodies use: a derived
    // sentence can embed a path / error / URL that carries a token shape, and
    // this is a NEW primary-text position (and the mermaid source is a new
    // export), so it must be masked like any other rendered session field.
    text: redactTokens(describe(e)),
    flags: e.flags ?? (e.flag ? [e.flag] : []),
    isError: e.type === "error",
  }))
}

// ── mermaid run graph ───────────────────────────────────────────────────────
// A `flowchart TD` built from the recorded order. Labels are sanitized for
// mermaid (quotes/brackets/pipes stripped). Capped so a long run stays legible;
// the overflow is stated, never silently dropped.
const GRAPH_CAP = 40
// Sessions reach 10^5+ events. This is a top-of-run narrative, not the full
// log — the virtualized Timeline below renders the complete stream. Cap the DOM
// rows here (stated, never silently) so a huge session can't freeze the tab.
const RENDER_CAP = 200

function mermaidLabel(text: string): string {
  return clip(text.replace(/["[\]{}()|<>#]/g, ""), 48) || "step"
}

function buildMermaid(steps: Step[], totalSteps: number): { src: string; truncated: number } {
  const shown = steps.slice(0, GRAPH_CAP)
  const truncated = totalSteps - shown.length
  const lines: string[] = ["flowchart TD", '    start(["session start"])']
  let prev = "start"
  shown.forEach((s, i) => {
    const id = `s${i}`
    const flagged = s.flags.length > 0 || s.isError
    lines.push(`    ${id}["${mermaidLabel(s.text)}"]${flagged ? ":::flagged" : ""}`)
    lines.push(`    ${prev} --> ${id}`)
    prev = id
  })
  if (truncated > 0) {
    lines.push(`    more["+ ${truncated} more steps"]`)
    lines.push(`    ${prev} --> more`)
  }
  lines.push("    classDef flagged stroke:#b91c1c,stroke-width:2px")
  return { src: lines.join("\n"), truncated }
}

interface CausalReplayProps {
  events: SessionEvent[]
  /** Name lookup for `custom:<uuid>` flags — see TimelineEvent. */
  customRuleInfo?: Record<string, CustomRuleInfo>
}

function shortId(kind: string): string {
  return kind.slice("custom:".length, "custom:".length + 8)
}

export function CausalReplay({ events, customRuleInfo }: CausalReplayProps) {
  const total = events.length
  // Describe + redact ONLY the steps we actually render/graph (RENDER_CAP ≥
  // GRAPH_CAP). A session can hold 10^5 events; redacting every one at load
  // would freeze the main thread for a view that shows the first RENDER_CAP.
  const steps = useMemo(() => buildSteps(events.slice(0, RENDER_CAP)), [events])
  const hiddenSteps = total - steps.length
  const { src, truncated } = useMemo(() => buildMermaid(steps, total), [steps, total])
  const [copied, setCopied] = useState(false)
  const [showGraph, setShowGraph] = useState(false)

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(src)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // clipboard unavailable — the source is selectable in the block below
    }
  }, [src])

  if (steps.length === 0) {
    return (
      <section aria-label="Plain-English replay" className="rounded-sm border border-rule bg-surface px-4 py-3">
        <h2 className="font-mono text-micro uppercase tracking-wider text-ink-faint">Plain-English replay</h2>
        <p className="mt-2 text-sm text-ink-muted">No events recorded for this session yet.</p>
      </section>
    )
  }

  return (
    <section aria-labelledby="replay-heading" className="rounded-sm border border-rule bg-surface">
      <div className="border-b border-rule px-4 py-3">
        <h2 id="replay-heading" className="font-mono text-micro uppercase tracking-wider text-ink-faint">
          Plain-English replay
        </h2>
        <p className="mt-1 text-caption text-ink-muted">
          The run as steps, derived from the recorded order. {total} step
          {total === 1 ? "" : "s"}.
        </p>
      </div>

      <ol className="divide-y divide-rule">
        {steps.map((s, i) => (
          <li key={s.key} id={`step-${s.key}`} className="flex gap-3 px-4 py-2.5">
            <span className="mt-0.5 shrink-0 font-mono text-micro tabular-nums text-ink-faint">
              {String(i + 1).padStart(2, "0")}
            </span>
            <div className="min-w-0 flex-1">
              <p className={"text-sm " + (s.isError ? "text-flag-env" : "text-ink")}>{s.text}</p>
              {s.flags.length > 0 && (
                <p className="mt-1 flex flex-wrap gap-1.5">
                  {s.flags.map((f) => (
                    <span
                      key={f}
                      className="inline-flex items-center rounded-sm border border-flag-secret/40 bg-flag-secret/5 px-1.5 py-0.5 font-mono text-micro uppercase tracking-wider text-flag-secret"
                    >
                      {isCustomFlag(f)
                        ? (customRuleInfo?.[f]?.name ?? `custom: ${shortId(f)}`)
                        : (FLAG_LABEL[f] ?? f)}
                    </span>
                  ))}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>

      {hiddenSteps > 0 && (
        <p className="border-t border-rule px-4 py-2.5 text-caption text-ink-faint">
          Showing the first {RENDER_CAP} of {total} steps. The full stream is in
          the timeline below.
        </p>
      )}

      <div className="border-t border-rule px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => setShowGraph((v) => !v)}
            className="rounded border border-rule px-2 py-1 font-mono text-caption text-ink-muted hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            aria-expanded={showGraph}
          >
            {showGraph ? "Hide run graph" : "Show run graph"}
          </button>
          {showGraph && (
            <button
              type="button"
              onClick={() => { void copy() }}
              className="rounded border border-rule px-2 py-1 font-mono text-caption text-ink-muted hover:bg-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
            >
              {copied ? "Copied" : "Copy mermaid"}
            </button>
          )}
          <span className="font-mono text-micro text-ink-faint">
            Mermaid source — renders in any mermaid viewer{truncated > 0 ? ` · first ${GRAPH_CAP} steps` : ""}
          </span>
        </div>
        {showGraph && (
          <pre
            className="mt-3 overflow-x-auto rounded-sm bg-sunken p-3 font-mono text-caption leading-relaxed text-ink-muted"
            aria-label="Run graph as mermaid source"
          >
            <code>{src}</code>
          </pre>
        )}
      </div>
    </section>
  )
}
