import type { FileChanged, FileOp, RedFlagKind, SessionDetail, CustomRuleInfo } from "./types"
import { RedFlagBadge, isCustomFlag } from "./RedFlagBadge"
import { ScorePanel } from "./ScorePanel"
import { TokenPanel } from "./TokenPanel"

interface FileChangedRailProps {
  session: SessionDetail
  /** Name/severity lookup for `custom:<uuid>` flags — see TimelineEvent. */
  customRuleInfo?: Record<string, CustomRuleInfo>
}

const OP_CLASS: Record<FileOp, string> = {
  create: "bg-sunken text-ink ring-rule",
  edit:   "bg-accent-500/10 text-accent-500 ring-accent-500/40",
  delete: "bg-flag-env-bg text-flag-env-fg ring-flag-env/60",
}

const RUBRIC = "font-mono text-caption uppercase tracking-wider text-ink-faint"

export function FileChangedRail({ session, customRuleInfo }: FileChangedRailProps) {
  const files = session.files_changed ?? []
  // Reverse: newest flagged event at the top of the rail, matching the
  // Timeline + FilesPanel ordering convention ("latest first").
  const flagEvents = (session.events ?? []).filter((e) => e.flag).slice().reverse()

  return (
    <aside
      aria-label="Session rail"
      // Sticky column with its OWN scroll track — the timeline on the left
      // and the rail on the right scroll independently. Without this the
      // rail's combined panel height could exceed the viewport and users
      // had to scroll the whole page to reach the Token panel underneath
      // Red Flags (or vice-versa). Inner overflow-y-auto confines scroll
      // to the rail, so moving the mouse wheel on the left of the screen
      // moves the timeline, on the right moves the rail.
      className={
        "space-y-4 lg:sticky lg:top-4 lg:self-start " +
        "lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto lg:pr-2"
      }
    >
      <ScorePanel score={session.score} />
      <FlagsPanel flags={session.flags} flagEvents={flagEvents} customRuleInfo={customRuleInfo} />
      {/* TokenPanel renders only when usage (kind=token) events are present;
          returns null otherwise (e.g. the redacted public session payload). */}
      <TokenPanel session={session} />
      <FilesPanel files={files} />
    </aside>
  )
}

function FilesPanel({ files }: { files: FileChanged[] }) {
  return (
    <section
      aria-label="Files changed"
      className="rounded-sm border border-rule bg-surface"
    >
      <header className="flex items-baseline justify-between border-b border-dashed border-rule px-4 py-2">
        <h2 className={RUBRIC}>Files changed</h2>
        <span className={`${RUBRIC} tabular-nums`}>
          {files.length} file{files.length === 1 ? "" : "s"}
        </span>
      </header>
      {files.length === 0 ? (
        <p className="px-4 py-3 font-sans text-caption text-ink-muted">
          No file changes recorded.
        </p>
      ) : (
        <div className="max-h-[28rem] overflow-y-auto">
          <ul>
            {files.map((file) => (
              <li key={file.path} className="border-b border-dashed border-rule last:border-b-0">
                <div
                  className="flex items-center gap-2 px-4 py-2"
                  title={file.path}
                >
                  <span
                    className={`inline-flex shrink-0 items-center rounded-sm px-1.5 py-0.5 font-mono text-micro font-semibold uppercase tracking-wider ring-1 ring-inset ${OP_CLASS[file.op]}`}
                  >
                    {file.op}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-caption text-ink">
                    {file.path}
                  </span>
                  <span className="shrink-0 font-mono text-caption tabular-nums text-accent-500">
                    +{file.additions}
                  </span>
                  <span className="shrink-0 font-mono text-caption tabular-nums text-flag-env-fg">
                    −{file.deletions}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

interface FlagsPanelProps {
  flags: RedFlagKind[]
  flagEvents: SessionDetail["events"]
  customRuleInfo?: Record<string, CustomRuleInfo>
}

function FlagsPanel({ flags, flagEvents, customRuleInfo }: FlagsPanelProps) {
  if (flags.length === 0 && flagEvents.length === 0) return null

  // Count flag events per kind. An event can carry MULTIPLE flags
  // (e.g. [secret_aws, shell_rm]) — count all of them, not just the first,
  // otherwise a session-level kind that only appears as a secondary flag
  // would show "×0" in the header.
  const countsByKind = new Map<RedFlagKind, number>()
  for (const e of flagEvents) {
    const eventFlags = e.flags ?? (e.flag ? [e.flag] : [])
    for (const f of eventFlags) {
      countsByKind.set(f, (countsByKind.get(f) ?? 0) + 1)
    }
  }
  // Preserve session.flags order (backend emits them by severity) but hide
  // kinds with zero event-level count (normally impossible; defensive guard).
  const kindsWithCounts = flags
    .map((k) => [k, countsByKind.get(k) ?? 0] as const)
    .filter(([, count]) => count > 0)
  const totalEvents = flagEvents.length

  return (
    <section
      aria-label="Red flags"
      className="rounded-sm border border-rule bg-surface"
    >
      <header className="flex items-baseline justify-between border-b border-dashed border-rule px-4 py-2">
        <h2 className={RUBRIC}>Red flags</h2>
        <span className={`${RUBRIC} tabular-nums`}>
          {totalEvents} event{totalEvents === 1 ? "" : "s"}
        </span>
      </header>

      {kindsWithCounts.length > 0 && (
        <ul className="divide-y divide-dashed divide-rule">
          {kindsWithCounts.map(([kind, count]) => (
            <li key={kind} className="flex items-center gap-2 px-4 py-1.5">
              <RedFlagBadge
                kind={kind}
                label={isCustomFlag(kind) ? customRuleInfo?.[kind]?.name : undefined}
                severity={isCustomFlag(kind) ? customRuleInfo?.[kind]?.severity : undefined}
              />
              <span className="ml-auto font-mono text-caption tabular-nums text-ink-muted">
                ×{count}
              </span>
            </li>
          ))}
        </ul>
      )}

      {flagEvents.length > 0 && (
        <div
          className={
            "border-t border-dashed border-rule " +
            // Cap visual height of the rail so a session with 50+ flagged
            // events doesn't take over the page; internal scroll keeps the
            // sticky sidebar workable. UI/UX Pro §5 content-priority: fold
            // long lists, §10 virtualize-lists kicks in past ~50 items.
            "max-h-[28rem] overflow-y-auto"
          }
        >
          <ul>
            {flagEvents.map((event) => (
              <li
                key={event.id}
                className="border-b border-dashed border-rule last:border-b-0"
              >
                <a
                  href={`#event-${event.id}`}
                  onClick={(e) => {
                    // Emit a focus event the Timeline listens for: expands
                    // the matching row, auto-grows the pagination window
                    // to include it, and scrolls + flashes. Falls back to
                    // plain hash navigation if no listener is mounted.
                    e.preventDefault()
                    window.history.replaceState(null, "", `#event-${event.id}`)
                    window.dispatchEvent(
                      new CustomEvent("receipt:focus-event", {
                        detail: { id: String(event.id) },
                      }),
                    )
                  }}
                  className="group flex items-center gap-2 px-4 py-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 focus-visible:ring-offset-2 focus-visible:ring-offset-paper hover:bg-sunken/40"
                >
                  {event.flag && (
                    <RedFlagBadge
                      kind={event.flag}
                      label={isCustomFlag(event.flag) ? customRuleInfo?.[event.flag]?.name : undefined}
                      severity={isCustomFlag(event.flag) ? customRuleInfo?.[event.flag]?.severity : undefined}
                    />
                  )}
                  <span className="min-w-0 flex-1 truncate font-mono text-caption text-ink-muted">
                    {event.file_path ?? event.tool_name ?? event.text ?? "flagged event"}
                  </span>
                  <span aria-hidden className="shrink-0 font-mono text-micro text-ink-faint">
                    ↗
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
