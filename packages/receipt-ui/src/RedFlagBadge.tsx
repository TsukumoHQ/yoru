import { memo } from "react"
import { Badge, type FlagKind } from "./Badge"
import type { RedFlagKind } from "./types"

// The 6 built-in presets — a closed, load-bearing set (do NOT add a 7th
// preset here; org-defined rules are the `custom:<uuid>` case handled below).
type PresetKind = Exclude<RedFlagKind, `custom:${string}`>

/** True for an org-defined rule hit (`custom:<uuid>`), false for one of the
 *  6 built-in presets. */
export function isCustomFlag(kind: RedFlagKind): kind is `custom:${string}` {
  return kind.startsWith("custom:")
}

// Static literal maps so Tailwind JIT resolves every utility referenced via
// Badge's per-kind class bundle. No template-string composition — self-learning
// §static-class-literal-maps.
const KIND_MAP: Record<PresetKind, FlagKind> = {
  "secret-pattern":   "secret",
  "env-mutation":     "env",
  "shell-destructive": "shell",
  "db-destructive":   "db",
  "migration-edit":   "migration",
  "ci-config-edit":   "ci",
}

const LABEL: Record<PresetKind, string> = {
  "secret-pattern":   "secret",
  "env-mutation":     "env",
  "shell-destructive": "shell",
  "db-destructive":   "db",
  "migration-edit":   "migration",
  "ci-config-edit":   "ci",
}

interface RedFlagBadgeProps {
  kind: RedFlagKind
  /** Display name for a `custom:<uuid>` kind (the org's rule name, resolved
   *  by the caller — this component never fetches). Ignored for the 6
   *  presets. Falls back to a short id slice when omitted, so a custom hit
   *  is never silently dropped even before the name has loaded. */
  label?: string
  /** The custom rule's user-configured severity ("critical"|"high"|"medium"),
   *  shown in the tooltip. Ignored for the 6 presets (their severity is
   *  implicit in the kind). */
  severity?: string
  className?: string
  onClick?: () => void
}

function RedFlagBadgeImpl({ kind, label, severity, className, onClick }: RedFlagBadgeProps) {
  const custom = isCustomFlag(kind)
  const badgeKind: FlagKind = custom ? "custom" : KIND_MAP[kind as PresetKind]
  const children = custom
    ? (label ?? `custom: ${kind.slice("custom:".length, "custom:".length + 8)}`)
    : LABEL[kind as PresetKind]
  const title = custom
    ? (severity ? `${children} · ${severity}` : children)
    : kind
  const badge = (
    <Badge kind={badgeKind} className={className} title={title}>
      {children}
    </Badge>
  )
  if (!onClick) return badge
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Open red-flag legend for ${children}`}
      className={
        "inline-flex cursor-pointer rounded-sm " +
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500 " +
        "focus-visible:ring-offset-2 focus-visible:ring-offset-paper"
      }
    >
      {badge}
    </button>
  )
}

export const RedFlagBadge = memo(RedFlagBadgeImpl)
