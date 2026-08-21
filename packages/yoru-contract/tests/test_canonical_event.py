from __future__ import annotations

from datetime import datetime, timezone

from yoru_contract import (
    SCHEMA_VERSION,
    Actor,
    Artifact,
    CanonicalEvent,
    Diff,
    Tool,
)


def _full_event() -> CanonicalEvent:
    return CanonicalEvent(
        session_id="sess-123",
        ts=datetime(2026, 8, 21, tzinfo=timezone.utc),
        actor=Actor(identity_id="tok-abc", machine_hostname="mac-air"),
        agent_kind="claude-code",
        agent_confidence="declared",
        action="file_change",
        tool=Tool(name="Write", args_digest="deadbeef"),
        artifact=Artifact(path="src/app.py", kind="file"),
        diff=Diff(unified_diff="@@ ...", stat="+3 -1"),
        content_ref="ref-1",
        source="adapter:claude-code",
    )


def test_round_trip_serialize_deserialize_full_event() -> None:
    event = _full_event()
    raw = event.model_dump_json()
    restored = CanonicalEvent.model_validate_json(raw)
    assert restored == event


def test_schema_version_defaults() -> None:
    event = _full_event()
    assert event.schema_version == SCHEMA_VERSION


def test_independent_capture_floor_omits_tool_and_content_ref() -> None:
    # The honest floor from design doc §B.3: independent-only capture
    # populates actor/artifact/diff but never tool.name or content_ref.
    event = CanonicalEvent(
        session_id="sess-456",
        ts=datetime(2026, 8, 21, tzinfo=timezone.utc),
        actor=Actor(identity_id="tok-abc"),
        agent_kind="unknown",
        agent_confidence="inferred",
        action="file_change",
        artifact=Artifact(path="src/app.py", kind="file"),
        diff=Diff(unified_diff="@@ ..."),
        source="independent:git",
    )
    raw = event.model_dump_json()
    restored = CanonicalEvent.model_validate_json(raw)
    assert restored.tool is None
    assert restored.content_ref is None
    assert restored == event


def test_old_client_missing_new_fields_still_validates() -> None:
    # schema_version graceful degradation: a minimal payload (as an old CLI
    # might send before it knows about optional fields) must still validate —
    # the backend branches on schema_version instead of hard-failing.
    minimal = {
        "session_id": "sess-789",
        "ts": "2026-08-21T00:00:00Z",
        "actor": {"identity_id": "tok-abc"},
        "source": "independent:fswatch",
    }
    event = CanonicalEvent.model_validate(minimal)
    assert event.schema_version == SCHEMA_VERSION
    assert event.agent_kind == "unknown"
    assert event.agent_confidence == "unknown"
    assert event.action == "unknown"
    assert event.tool is None
