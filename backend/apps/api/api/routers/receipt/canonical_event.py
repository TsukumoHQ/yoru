"""EventIn -> CanonicalEvent translation (design doc trovex:4e85331d4fbe4cdd8c3cbf8ee7c6848b
§B.1/B.2, DEC-yoru-design-ruling-1).

The Claude Code hook/tailer does NOT emit a CanonicalEvent over the wire —
`EventIn` (Claude-Code-shaped) stays the ingest wire format until a second
real adapter exists to justify a wire change (that's ticket B3, the
independent git/fs capture floor). This module IS the CC adapter, expressed
server-side: it fills a `CanonicalEvent` from whatever an `EventIn` carries,
so `red_flags.scan_event` can be written against the agent-neutral envelope
(tool/artifact/diff/content_ref) instead of CC-specific field names, with
zero change to what the CLI actually sends this ticket.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from yoru_contract.canonical_event import ActionKind, Actor, Artifact, CanonicalEvent, Diff, Tool

if TYPE_CHECKING:
    from .models import EventIn

# EventKind -> ActionKind. Only the kinds a red-flag rule actually inspects
# map to something other than "unknown" — message/session_start/session_end
# never trigger a rule today, so folding them to "unknown" is lossless for
# detection purposes.
_ACTION_BY_KIND: dict[str, ActionKind] = {
    "tool_use": "tool_call",
    "file_change": "file_change",
    "commit": "commit",
}


def to_canonical_event(e: "EventIn") -> CanonicalEvent:
    """Best-effort translation. `actor.identity_id` is a placeholder
    (`e.user` or "unknown") — nothing in this ticket reads it; a real
    CliToken.id plumb-through is a follow-up for whenever the envelope's
    actor is actually consumed (attribution display, the B3 independent
    capture floor)."""
    if e.source == "independent:git":
        # B3 slice1 (trovex:4e85331d §D) — git hooks, zero agent
        # cooperation. Honestly "unknown", not "inferred": unlike an
        # fs-watch/PTY source this floor has no process-name signal to
        # infer FROM, so claiming "inferred" would overstate confidence.
        diff = (
            Diff(unified_diff=e.diff_unified, stat=e.diff_stat)
            if (e.diff_unified or e.diff_stat)
            else None
        )
        return CanonicalEvent(
            session_id=e.session_id,
            ts=e.ts or datetime.now(timezone.utc),
            actor=Actor(identity_id=e.user or "unknown"),
            agent_kind="unknown",
            agent_confidence="unknown",
            action=_ACTION_BY_KIND.get(e.kind or "", "unknown"),
            tool=None,
            artifact=None,
            diff=diff,
            content_ref=e.content,
            source="independent:git",
        )
    return CanonicalEvent(
        session_id=e.session_id,
        ts=e.ts or datetime.now(timezone.utc),
        actor=Actor(identity_id=e.user or "unknown"),
        agent_kind="claude-code",
        agent_confidence="declared",
        action=_ACTION_BY_KIND.get(e.kind or "", "unknown"),
        tool=Tool(name=e.tool) if e.tool else None,
        artifact=Artifact(path=e.path) if e.path else None,
        diff=None,
        content_ref=e.content,
        source="adapter:claude-code",
    )
