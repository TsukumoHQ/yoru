#!/usr/bin/env python3
"""
Simple test script to verify Cursor hook script functionality.
This tests the hook script in isolation without requiring the full backend.
"""
import json
import sys
from pathlib import Path

# Import the production parser
from cursor_hook import parse_cursor_event


def test_session_start_event():
    """Test sessionStart event parsing."""
    data = {
        "sessionId": "test-session-123",
        "hook": "sessionStart",
        "cwd": "/Users/test/project",
    }

    result = parse_cursor_event(data)
    assert result is not None
    assert result["session_id"] == "test-session-123"
    assert result["kind"] == "session_start"
    assert result["agent"] == "cursor"
    assert result["cwd"] == "/Users/test/project"
    print("PASS: sessionStart event parsed correctly")


def test_post_tool_use_event():
    """Test postToolUse event parsing."""
    data = {
        "sessionId": "test-session-123",
        "hook": "postToolUse",
        "tool": "bash",
        "toolInput": {"command": "ls -la"},
        "toolOutput": "file1.txt\nfile2.txt",
        "cwd": "/Users/test/project",
    }

    result = parse_cursor_event(data)
    assert result is not None
    assert result["session_id"] == "test-session-123"
    assert result["kind"] == "tool_use"
    assert result["tool"] == "bash"
    assert result["content"] == "ls -la"
    assert result["agent"] == "cursor"
    print("PASS: postToolUse event parsed correctly")


def test_after_file_edit_event():
    """Test afterFileEdit event parsing."""
    data = {
        "sessionId": "test-session-123",
        "hook": "afterFileEdit",
        "filePath": "/Users/test/project/src/main.py",
        "diff": "@@ -1,1 +1,1 @@",
        "cwd": "/Users/test/project",
    }

    result = parse_cursor_event(data)
    assert result is not None
    assert result["session_id"] == "test-session-123"
    assert result["kind"] == "file_change"
    assert result["path"] == "/Users/test/project/src/main.py"
    assert result["agent"] == "cursor"
    print("PASS: afterFileEdit event parsed correctly")


def test_ignored_event():
    """Test that unknown events are ignored."""
    data = {
        "sessionId": "test-session-123",
        "hook": "unknownEvent",
        "cwd": "/Users/test/project",
    }

    result = parse_cursor_event(data)
    assert result is None
    print("PASS: Unknown event correctly ignored")


def test_missing_session_id():
    """Test that events without sessionId are ignored."""
    data = {
        "hook": "sessionStart",
        "cwd": "/Users/test/project",
    }

    result = parse_cursor_event(data)
    assert result is None
    print("PASS: Event without sessionId correctly ignored")


if __name__ == "__main__":
    print("Testing Cursor hook script...")
    print()

    try:
        test_session_start_event()
        test_post_tool_use_event()
        test_after_file_edit_event()
        test_ignored_event()
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