import { memo } from "react"
import { Badge, type FlagKind } from "./Badge"
import type { RedFlagKind } from "./types"

// The 6 built-in presets — a closed, load-bearing set (do NOT add a 7th
// preset here; org-defined rules are the `custom:<uuid>` case handled below).
type PresetKind = Exclude<RedFlagKind, `custom:${string}` | `skill:${string}`>

/** True for an org-defined rule hit (`custom:<uuid>`), false for one of the
 *  6 built-in presets. */
export function isCustomFlag(kind: RedFlagKind): kind is `custom:${string}` {
  return kind.startsWith("custom:")
}

/** True for a built-in skill-safety rule hit (`skill:<id>`, task fa3baa27). */
export function isSkillFlag(kind: RedFlagKind): kind is `skill:${string}` {
  return kind.startsWith("skill:")
}

// Static labels for the 16-rule skill-safety catalog (backend/apps/api/api/
// routers/receipt/skill_safety.py `_RULES`) — a fixed built-in set, so
// (unlike `custom:`) the label never needs an org fetch. Keep in sync with
// that table's keys.
export const SKILL_RULE_LABEL: Record<string, string> = {
  "skill:shell-curl-pipe-shell": "curl | shell",
  "skill:shell-reverse-shell":   "reverse shell",
  "skill:shell-sensitive-exfil": "sensitive-path exfil",
  "skill:shell-destructive":     "destructive shell",
  "skill:shell-env-dump":        "env dump",
  "skill:path-credentials":      "credential path read",
  "skill:net-cloud-metadata":    "cloud metadata probe",
  "skill:net-raw-ip-endpoint":   "raw-IP endpoint",
  "skill:inject-override":       "prompt injection: override",
  "skill:inject-role-escape":    "prompt injection: role escape",
  "skill:inject-concealment":    "prompt injection: concealment",
  "skill:inject-invisible-text": "prompt injection: hidden text",
  "skill:secret-private-key":    "private key",
  "skill:secret-token":          "secret token",
  "skill:hook-secret-touch":     "hook + secret touch",
  "skill:pkg-structure":         "package-structure escape",
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
  const skill = isSkillFlag(kind)
  const badgeKind: FlagKind = custom ? "custom" : skill ? "skill" : KIND_MAP[kind as PresetKind]
  const children = custom
    ? (label ?? `custom: ${kind.slice("custom:".length, "custom:".length + 8)}`)
    : skill
      ? (SKILL_RULE_LABEL[kind] ?? kind.slice("skill:".length))
      : LABEL[kind as PresetKind]
  const title = custom || skill
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
