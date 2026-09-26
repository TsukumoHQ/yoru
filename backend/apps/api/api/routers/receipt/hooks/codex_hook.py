#!/usr/bin/env python3
"""
Yoru hook for Codex CLI.

Reads Codex hook events from stdin, transforms them to Yoru's event format,
and POSTs to /api/v1/sessions/events with agent="codex".

Designed to be non-blocking: exits 0 even if the backend is unreachable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Configuration paths
CONFIG_PATH = Path.home() / ".config" / "yoru" / "config.json"
DEFAULT_SERVER = "http://localhost:8002"
EVENTS_ENDPOINT = "/api/v1/sessions/events"


def load_config() -> dict:
    """Load Yoru config from ~/.config/yoru/config.json."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {"server": DEFAULT_SERVER}


def get_git_context(cwd: str | None) -> tuple[str | None, str | None]:
    """Get git remote and branch for the current working directory.

    Returns (git_remote, git_branch) or (None, None) if not in a git repo.
    """
    if not cwd:
        return None, None

    try:
        # Get git remote
        result = subprocess.run(
            ["git", "-C", cwd, "ls-remote", "--get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        git_remote = result.stdout.strip() if result.returncode == 0 else None

        # Get git branch
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        git_branch = result.stdout.strip() if result.returncode == 0 else None

        return git_remote, git_branch
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        # Git not available or timed out - don't block the hook
        return None, None


def parse_codex_event(data: dict) -> dict | None:
    """Transform Codex hook event to Yoru EventIn format.

    Returns None for events that should be ignored (e.g., permission requests).
    """
    hook_event = data.get("hook_event_name")
    session_id = data.get("session_id")
    cwd = data.get("cwd")

    if not session_id:
        return None

    # Get git context for routing
    git_remote, git_branch = get_git_context(cwd)

    # Map Codex hook events to Yoru event kinds
    event_mapping = {
        "SessionStart": "session_start",
        "SessionEnd": "session_end",
        "PreToolUse": "tool_use",
        "PostToolUse": "tool_use",
        "UserPromptSubmit": "message",
        "Stop": "session_end",
    }

    kind = event_mapping.get(hook_event)
    if not kind:
        # Skip events we don't handle (PermissionRequest, SubagentStart, etc.)
        return None

    # Extract tool information
    tool = data.get("tool_name")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response")

    # Extract path and content from tool_input
    path = None
    content = None

    if isinstance(tool_input, dict):
        # File operations
        path = tool_input.get("file_path") or tool_input.get("path")
        # Shell commands
        if tool == "Bash":
            content = tool_input.get("command")
        # Other tools
        if not content:
            content = tool_input.get("query") or tool_input.get("pattern")

    # For PostToolUse, also capture response content
    if tool_response and isinstance(tool_response, str):
        if not content:
            content = tool_response[:400]  # Cap at 400 chars

    # Extract user prompt for UserPromptSubmit
    if hook_event == "UserPromptSubmit":
        content = data.get("prompt", "")
        tool = "user"

    # Build Yoru event
    yoru_event = {
        "session_id": session_id,
        "kind": kind,
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "path": path,
        "content": content,
        "cwd": cwd,
        "git_remote": git_remote,
        "git_branch": git_branch,
        "agent": "codex",  # Specify this is a Codex event
        # Raw Codex event for debugging
        "raw": {"codex_hook": data},
    }

    return yoru_event


def send_event(server: str, token: str, event: dict) -> bool:
    """Send event to Yoru backend.

    Returns True if successful, False otherwise.
    Never raises - designed to be non-blocking.
    """
    url = f"{server.rstrip('/')}{EVENTS_ENDPOINT}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {"events": [event]}

    try:
        response = httpx.post(
            url,
            json=payload,
            headers=headers,
            timeout=2.0,  # Don't block Codex
        )
        return response.status_code in (200, 202)
    except Exception:
        # Silently fail - don't block Codex execution
        return False


def main():
    """Main hook entry point."""
    # Read JSON from stdin
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Invalid JSON - exit silently
        sys.exit(0)

    # Parse Codex event
    event = parse_codex_event(data)
    if not event:
        # Event not handled - exit successfully
        sys.exit(0)

    # Load config
    config = load_config()
    server = config.get("server", DEFAULT_SERVER)
    token = config.get("token")

    if not token:
        # No token configured - exit silently
        sys.exit(0)

    # Send event (non-blocking)
    send_event(server, token, event)

    # Always exit 0 - never block Codex
    sys.exit(0)


if __name__ == "__main__":
    main()