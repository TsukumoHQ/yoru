#!/usr/bin/env python3
"""
Simple test script to verify Codex hook script functionality.
This tests the hook script in isolation without requiring the full backend.
"""
import json
import sys
from pathlib import Path

# Import the production parser
from codex_hook import parse_codex_event


def test_session_start_event():
    """Test SessionStart event parsing."""
    data = {
        "session_id": "test-session-123",
        "hook_event_name": "SessionStart",
        "cwd": "/Users/test/project",
        "model": "gpt-4",
    }

    result = parse_codex_event(data)
    assert result is not None
    assert result["session_id"] == "test-session-123"
    assert result["kind"] == "session_start"
    assert result["agent"] == "codex"
    assert result["cwd"] == "/Users/test/project"
    print("PASS: SessionStart event parsed correctly")


def test_post_tool_use_event():
    """Test PostToolUse event parsing."""
    data = {
        "session_id": "test-session-123",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls -la"},
        "tool_response": "file1.txt\nfile2.txt",
        "cwd": "/Users/test/project",
        "model": "gpt-4",
    }

    result = parse_codex_event(data)
    assert result is not None
    assert result["session_id"] == "test-session-123"
    assert result["kind"] == "tool_use"
    assert result["tool"] == "Bash"
    assert result["content"] == "ls -la"
    assert result["agent"] == "codex"
    print("PASS: PostToolUse event parsed correctly")


def test_user_prompt_submit_event():
    """Test UserPromptSubmit event parsing."""
    data = {
        "session_id": "test-session-123",
        "hook_event_name": "UserPromptSubmit",
        "prompt": "Fix the bug in the authentication code",
        "cwd": "/Users/test/project",
        "model": "gpt-4",
    }

    result = parse_codex_event(data)
    assert result is not None
    assert result["session_id"] == "test-session-123"
    assert result["kind"] == "message"
    assert result["tool"] == "user"
    assert result["content"] == "Fix the bug in the authentication code"
    assert result["agent"] == "codex"
    print("PASS: UserPromptSubmit event parsed correctly")


def test_ignored_event():
    """Test that PermissionRequest events are ignored."""
    data = {
        "session_id": "test-session-123",
        "hook_event_name": "PermissionRequest",
        "cwd": "/Users/test/project",
        "model": "gpt-4",
    }

    result = parse_codex_event(data)
    assert result is None
    print("PASS: PermissionRequest event correctly ignored")


def test_file_operation_event():
    """Test file operation event parsing."""
    data = {
        "session_id": "test-session-123",
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": "/Users/test/project/src/main.py", "content": "print('hello')"},
        "cwd": "/Users/test/project",
        "model": "gpt-4",
    }

    result = parse_codex_event(data)
    assert result is not None
    assert result["session_id"] == "test-session-123"
    assert result["kind"] == "tool_use"
    assert result["tool"] == "Write"
    assert result["path"] == "/Users/test/project/src/main.py"
    assert result["agent"] == "codex"
    print("PASS: File operation event parsed correctly")


def test_missing_session_id():
    """Test that events without session_id are ignored."""
    data = {
        "hook_event_name": "SessionStart",
        "cwd": "/Users/test/project",
        "model": "gpt-4",
    }

    result = parse_codex_event(data)
    assert result is None
    print("PASS: Event without session_id correctly ignored")


if __name__ == "__main__":
    print("Testing Codex hook script...")
    print()

    try:
        test_session_start_event()
        test_post_tool_use_event()
        test_user_prompt_submit_event()
        test_ignored_event()
        test_file_operation_event()
        test_missing_session_id()

        print()
        print("PASS: All tests passed!")
        sys.exit(0)
    except AssertionError as e:
        print(f"FAIL: Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)