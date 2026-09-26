#!/usr/bin/env python3
"""
Yoru hook for Cursor Agent.

Reads Cursor hook events from stdin, transforms them to Yoru's event format,
and POSTs to /api/v1/sessions/events with agent="cursor".

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


def parse_cursor_event(data: dict) -> dict | None:
    """Transform Cursor hook event to Yoru EventIn format.

    Returns None for events that should be ignored.
    """
    hook_name = data.get("hook")
    session_id = data.get("sessionId")
    cwd = data.get("cwd")

    if not session_id:
        return None

    # Map Cursor hook names to Yoru event kinds
    event_mapping = {
        "sessionStart": "session_start",
        "sessionEnd": "session_end",
        "preToolUse": "tool_use",
        "postToolUse": "tool_use",
        "postToolUseFailure": "tool_use",
        "beforeShellExecution": "tool_use",
        "afterShellExecution": "tool_use",
        "beforeReadFile": "tool_use",
        "afterFileEdit": "file_change",
        "stop": "session_end",
    }

    kind = event_mapping.get(hook_name)
    if not kind:
        # Skip events we don't handle
        return None

    # Extract tool information
    tool = data.get("tool")
    tool_input = data.get("toolInput", {})
    tool_output = data.get("toolOutput")

    # Extract path and content from tool_input
    path = None
    content = None

    if isinstance(tool_input, dict):
        # File operations
        path = tool_input.get("file_path") or tool_input.get("path")
        # Shell commands
        if tool == "bash" or hook_name in ("beforeShellExecution", "afterShellExecution"):
            content = tool_input.get("command")
        # Other tools
        if not content:
            content = tool_input.get("query") or tool_input.get("pattern")

    # For post-execution hooks, capture output
    if tool_output and isinstance(tool_output, str):
        if not content:
            content = tool_output[:400]  # Cap at 400 chars

    # Special handling for file edits
    if hook_name == "afterFileEdit":
        path = data.get("filePath") or path
        content = data.get("diff") or content

    # Build Yoru event
    yoru_event = {
        "session_id": session_id,
        "kind": kind,
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "path": path,
        "content": content,
        "cwd": cwd,
        "agent": "cursor",  # Specify this is a Cursor event
        # Raw Cursor event for debugging
        "raw": {"cursor_hook": data},
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
            timeout=2.0,  # Don't block Cursor
        )
        return response.status_code in (200, 202)
    except Exception:
        # Silently fail - don't block Cursor execution
        return False


def main():
    """Main hook entry point."""
    # Read JSON from stdin
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Invalid JSON - exit silently
        sys.exit(0)

    # Parse Cursor event
    event = parse_cursor_event(data)
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

    # Always exit 0 - never block Cursor
    sys.exit(0)


if __name__ == "__main__":
    main()