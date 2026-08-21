"""CanonicalEvent — the agent-neutral event envelope (design doc §B.2,
trovex:4e85331d4fbe4cdd8c3cbf8ee7c6848b).

Sits in front of today's Claude-Code-shaped `EventIn`: the CC tailer becomes
one adapter that can fill every field, other capture sources (git/fs-watch,
PTY-wrap) fill it partially. `schema_version` lets a backend that's ahead of
an old CLI's contract branch instead of hard-failing — old CLIs just leave
new fields empty, same graceful-degradation posture as the independent
capture floor.

Schema only: no business logic, no red-flag detection, no DB models.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SCHEMA_VERSION = "1"

AgentKind = str  # "claude-code" | "cursor" | "unknown" | ... — open-ended, not an enum
AgentConfidence = Literal["declared", "inferred", "unknown"]
ActionKind = Literal["tool_call", "file_change", "commit", "process_exec", "unknown"]
EventSource = Literal[
    "adapter:claude-code",
    "adapter:cursor",
    "adapter:codex",
    "adapter:aider",
    "adapter:gemini-cli",
    "adapter:cline",
    "independent:git",
    "independent:fswatch",
    "independent:pty",
]


class Actor(BaseModel):
    """WHO — from pairing (CliToken identity), never from the agent itself."""

    identity_id: str
    machine_hostname: str | None = None


class Tool(BaseModel):
    """Populated when an adapter/PTY can name the tool invoked; absent on the
    independent-only capture floor (git/fs can't see tool-call intent)."""

    name: str | None = None
    args_digest: str | None = None


class Artifact(BaseModel):
    """The file/dir touched, when known."""

    path: str | None = None
    kind: str | None = None


class Diff(BaseModel):
    """Populated from git when available, regardless of agent cooperation —
    this is the independent-capture floor's strongest signal."""

    unified_diff: str | None = None
    stat: str | None = None


class CanonicalEvent(BaseModel):
    schema_version: str = SCHEMA_VERSION
    session_id: str
    ts: datetime
    actor: Actor
    agent_kind: AgentKind = "unknown"
    agent_confidence: AgentConfidence = "unknown"
    action: ActionKind = "unknown"
    tool: Tool | None = None
    artifact: Artifact | None = None
    diff: Diff | None = None
    content_ref: str | None = None
    source: EventSource
