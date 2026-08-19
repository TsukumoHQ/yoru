"""OTel-GenAI projection — render yoru sessions/events as OpenTelemetry spans.

S1 of the compliance-grade audit-trail design (trovex:28547568, ticket
d6ec19c6). READ-SIDE ONLY — this module never touches the DB and never changes
storage. It maps the existing ``Session``/``Event`` rows onto the OpenTelemetry
GenAI semantic conventions so the trail is *interop, not silo*: any OTel backend
(Jaeger, Tempo, an auditor's collector) can ingest it.

Two rules from the brief, honored here:

  1. **Content lives in span EVENTS, not attributes.** Attributes carry only
     queryable, non-sensitive metadata (``gen_ai.*`` + the ``yoru.*`` overlay);
     the prompt / command / file CONTENT (tool_input, tool_response, message
     text) is emitted as span *events* (log-records), which a collector — or a
     future yoru redaction pass (S4) — can drop without touching the attributes
     or breaking span structure.
  2. **yoru-namespaced compliance overlay** on valid OTel: ``yoru.event.hash`` /
     ``yoru.event.prev_hash`` (the tamper-evidence chain), ``yoru.redflag.*``,
     ``yoru.retention.expires_at``, ``yoru.cost.usd``.

Mapping (design §1a):
  - Session      -> ``invoke_agent`` span (the conversation root)
  - tool_use /
    file_change  -> ``execute_tool`` child span
  - token        -> ``chat`` child span
  - message      -> ``gen_ai.{user,assistant}.message`` content events on the root

Determinism: trace/span ids are derived by hashing stable identifiers (session
id, event id), never randomly. Same trail -> byte-identical ids, so an OTLP
export is reproducible — a property the auditor story (S6 signed export) leans
on later.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Optional

# OTLP span.kind enum (opentelemetry-proto trace.proto). GenAI semconv marks
# execute_tool INTERNAL; agent/conversation spans INTERNAL; a model call is a
# CLIENT span (the app is the client of the model).
_KIND_INTERNAL = 1
_KIND_CLIENT = 3

# Instrumentation scope stamped on every emitted trace. Bumping the version is
# how a downstream consumer detects a projection-shape change.
_SCOPE_NAME = "yoru"
_SCOPE_VERSION = "1"


# ── id + time helpers ────────────────────────────────────────────────────────

def _trace_id(session_id: str) -> str:
    """32-hex (16-byte) OTLP trace id, deterministic per session."""
    return hashlib.sha256(f"trace:{session_id}".encode("utf-8")).hexdigest()[:32]


def _span_id(*parts: str) -> str:
    """16-hex (8-byte) OTLP span id, deterministic per (session, marker)."""
    basis = ":".join(parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _nanos(dt: Optional[datetime]) -> str:
    """OTLP *UnixNano timestamp as a string. Naive datetimes are treated as
    UTC (the store persists naive-UTC). None -> "0" (unknown)."""
    if dt is None:
        return "0"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp() * 1_000_000_000))


# ── OTLP AnyValue encoders ───────────────────────────────────────────────────
# proto3-JSON maps int64 to a *string* to survive JS number precision; we follow
# that so the output is valid against the OTLP/JSON schema.

def _kv(key: str, value: Any) -> Optional[dict]:
    """Encode one OTLP KeyValue, or None to omit (falsy-but-not-zero values are
    dropped so we don't emit empty attributes)."""
    if value is None:
        return None
    if isinstance(value, bool):
        av: dict = {"boolValue": value}
    elif isinstance(value, int):
        av = {"intValue": str(value)}
    elif isinstance(value, float):
        av = {"doubleValue": value}
    elif isinstance(value, (list, tuple)):
        if not value:
            return None
        av = {"arrayValue": {"values": [{"stringValue": str(v)} for v in value]}}
    else:
        s = value if isinstance(value, str) else str(value)
        if s == "":
            return None
        av = {"stringValue": s}
    return {"key": key, "value": av}


def _attrs(pairs: dict) -> list[dict]:
    """Encode a {key: value} dict to an OTLP attribute list, dropping Nones."""
    out = []
    for k, v in pairs.items():
        kv = _kv(k, v)
        if kv is not None:
            out.append(kv)
    return out


def _provider_of_model(model: Optional[str], agent: Optional[str]) -> Optional[str]:
    """Best-effort ``gen_ai.provider.name`` from the model string / agent slug.
    yoru's source today is Claude Code -> anthropic; kept as a small lookup so
    a future non-Claude source projects correctly instead of mislabeling."""
    m = (model or "").lower()
    if "claude" in m or (agent or "").lower() == "claude-code":
        return "anthropic"
    if "gpt" in m or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if "gemini" in m:
        return "gcp.gen_ai"
    return None


# ── normalized event shape ───────────────────────────────────────────────────
# The core builder consumes plain dicts so the SAME projection serves both the
# owner path (full fidelity, from Event rows) and the public path (pre-redacted,
# from PublicEventOut) — the public endpoint structurally cannot leak because it
# only ever passes data the public JSON endpoint already exposes.

def _tool_span(session_id: str, root_span: str, ev: dict) -> dict:
    """Build an ``execute_tool`` span from a normalized event dict."""
    sid = _span_id("event", session_id, str(ev["id"]))
    tool = ev.get("tool")
    attributes = _attrs({
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": tool,
        "gen_ai.tool.call.id": ev.get("entry_uuid"),
        # yoru compliance overlay (non-sensitive metadata only)
        "yoru.event.hash": ev.get("entry_hash"),
        "yoru.event.prev_hash": ev.get("prev_hash"),
        "yoru.file.path": ev.get("path"),
        "yoru.redflag.type": ev.get("flags") or None,
        "yoru.redflag.severity": ev.get("redflag_severity"),
        "yoru.retention.expires_at": ev.get("retention_expires_at"),
    })
    # CONTENT goes in a span event, never an attribute (redactable channel).
    span_events = []
    args = ev.get("tool_input")
    result = ev.get("tool_output")
    content = ev.get("content")
    body = _attrs({
        "gen_ai.tool.call.arguments": _as_text(args),
        "gen_ai.tool.call.result": _as_text(result),
        "content": content,
    })
    if body:
        span_events.append({
            "timeUnixNano": _nanos(ev.get("ts")),
            "name": "gen_ai.tool.message",
            "attributes": body,
        })
    return {
        "traceId": _trace_id(session_id),
        "spanId": sid,
        "parentSpanId": root_span,
        "name": f"execute_tool {tool}" if tool else "execute_tool",
        "kind": _KIND_INTERNAL,
        "startTimeUnixNano": _nanos(ev.get("ts")),
        "endTimeUnixNano": _nanos(ev.get("ts")),
        "attributes": attributes,
        "events": span_events,
    }


def _chat_span(session_id: str, root_span: str, ev: dict, *,
               provider: Optional[str]) -> dict:
    """Build a ``chat`` span from a normalized token event dict."""
    sid = _span_id("event", session_id, str(ev["id"]))
    model = ev.get("model")
    attributes = _attrs({
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": model,
        "gen_ai.response.model": model,
        # Store maps to the modern OTel names (not the deprecated
        # prompt/completion aliases the columns are historically named after).
        "gen_ai.usage.input_tokens": ev.get("tokens_input") or None,
        "gen_ai.usage.output_tokens": ev.get("tokens_output") or None,
        "gen_ai.response.finish_reasons": ev.get("finish_reasons") or None,
        "yoru.cost.usd": ev.get("cost_usd") or None,
        "yoru.event.hash": ev.get("entry_hash"),
        "yoru.event.prev_hash": ev.get("prev_hash"),
        "yoru.retention.expires_at": ev.get("retention_expires_at"),
    })
    return {
        "traceId": _trace_id(session_id),
        "spanId": sid,
        "parentSpanId": root_span,
        "name": f"chat {model}" if model else "chat",
        "kind": _KIND_CLIENT,
        "startTimeUnixNano": _nanos(ev.get("ts")),
        "endTimeUnixNano": _nanos(ev.get("ts")),
        "attributes": attributes,
        "events": [],
    }


def _message_event(ev: dict) -> Optional[dict]:
    """A message event -> an OTel GenAI content event on the conversation root.
    user -> gen_ai.user.message; assistant/thinking -> gen_ai.assistant.message."""
    role = (ev.get("tool") or "").lower()
    content = ev.get("content")
    if not content:
        return None
    if role == "user":
        name = "gen_ai.user.message"
    else:
        name = "gen_ai.assistant.message"
    body = {"content": content}
    if role == "thinking":
        body["yoru.message.channel"] = "thinking"
    return {
        "timeUnixNano": _nanos(ev.get("ts")),
        "name": name,
        "attributes": _attrs(body),
    }


def _as_text(v: Any) -> Optional[str]:
    """Serialize a content payload (dict/list/str) to a string for the event
    body. dict/list -> compact JSON so the structure survives the projection."""
    if v is None:
        return None
    if isinstance(v, str):
        return v or None
    try:
        return json.dumps(v, default=str, ensure_ascii=False)
    except Exception:
        return str(v)


def build_trace(session: dict, events: list[dict]) -> dict:
    """Assemble one OTLP/JSON ExportTraceServiceRequest for a single session.

    ``session`` is a normalized dict of the conversation-root fields; ``events``
    a list of normalized event dicts (see ``normalize_row_event`` /
    ``normalize_public_event``). Returns a resourceSpans structure ready to be
    JSON-serialized and shipped to any OTLP consumer.
    """
    session_id = session["id"]
    root_span = _span_id("session", session_id)
    provider = _provider_of_model(session.get("model"), session.get("agent"))

    root_attrs = _attrs({
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.conversation.id": session_id,
        "gen_ai.agent.id": session.get("agent"),
        "gen_ai.agent.name": session.get("agent"),
        "gen_ai.provider.name": provider,
        "gen_ai.request.model": session.get("model"),
        "enduser.id": session.get("user"),  # dropped on the public path
        # yoru overlay
        "yoru.session.grade": session.get("grade"),
        "yoru.session.score": session.get("score"),
        "yoru.flagged": session.get("flagged"),
        "yoru.workspace.id": session.get("workspace_id"),
        "yoru.git.remote": session.get("git_remote"),
        "yoru.git.branch": session.get("git_branch"),
        "yoru.retention.expires_at": session.get("retention_expires_at"),
    })

    child_spans: list[dict] = []
    message_events: list[dict] = []
    for ev in events:
        kind = ev.get("kind")
        if kind in ("tool_use", "file_change"):
            child_spans.append(_tool_span(session_id, root_span, ev))
        elif kind == "token":
            child_spans.append(_chat_span(session_id, root_span, ev, provider=provider))
        elif kind == "message":
            me = _message_event(ev)
            if me is not None:
                message_events.append(me)
        # session_start/session_end/error carry no GenAI span of their own; the
        # root span's start/end already encode the lifetime, and errors surface
        # via the tool span they attach to.

    root = {
        "traceId": _trace_id(session_id),
        "spanId": root_span,
        "parentSpanId": "",
        "name": f"invoke_agent {session.get('agent')}" if session.get("agent") else "invoke_agent",
        "kind": _KIND_INTERNAL,
        "startTimeUnixNano": _nanos(session.get("started_at")),
        "endTimeUnixNano": _nanos(session.get("ended_at") or session.get("started_at")),
        "attributes": root_attrs,
        "events": message_events,
    }

    resource_attrs = _attrs({
        "service.name": "yoru",
        "gen_ai.agent.name": session.get("agent"),
    })
    return {
        "resourceSpans": [{
            "resource": {"attributes": resource_attrs},
            "scopeSpans": [{
                "scope": {"name": _SCOPE_NAME, "version": _SCOPE_VERSION},
                "spans": [root, *child_spans],
            }],
        }],
    }


# ── normalizers: map storage / public models -> the normalized event dict ─────

def normalize_row_event(e) -> dict:
    """Full-fidelity normalization of an ``Event`` ORM row (owner path).

    Carries the UNCAPPED raw tool_input / tool_response — the auditor wants the
    real payload, not the truncated timeline preview — plus the chain hashes and
    (S2, when present) the retention/severity overlay.
    """
    raw = e.raw if isinstance(getattr(e, "raw", None), dict) else {}
    tool = e.tool or (raw.get("tool_name") if isinstance(raw.get("tool_name"), str) else None)
    model = None
    finish = None
    if e.kind == "token":
        model = raw.get("model") if isinstance(raw.get("model"), str) else None
        fr = raw.get("finish_reasons") or raw.get("stop_reason")
        if fr is not None:
            finish = fr if isinstance(fr, list) else [fr]
    return {
        "id": e.id if e.id is not None else (e.entry_uuid or ""),
        "kind": e.kind,
        "ts": e.ts,
        "tool": tool,
        "path": e.path,
        "content": e.content,
        "tool_input": raw.get("tool_input"),
        "tool_output": raw.get("tool_response"),
        "tokens_input": e.tokens_input,
        "tokens_output": e.tokens_output,
        "cost_usd": e.cost_usd,
        "model": model,
        "finish_reasons": finish,
        "flags": list(e.flags or []),
        "entry_hash": e.entry_hash,
        "prev_hash": e.prev_hash,
        "entry_uuid": e.entry_uuid,
        # S2 overlay columns — read defensively so S1 lands before S2.
        "retention_expires_at": _iso_opt(getattr(e, "retention_expires_at", None)),
        "redflag_severity": getattr(e, "redflag_severity", None),
    }


def normalize_public_event(pe) -> dict:
    """Normalization of an already-redacted ``PublicEventOut`` (public path).

    Content here is whatever the public JSON endpoint already exposes: scrubbed,
    and fully stripped on ``secret_*`` events. NO chain hashes, NO raw. So the
    public OTLP is a pure re-shape of the public JSON — it cannot leak anything
    the JSON endpoint doesn't already.
    """
    return {
        "id": pe.id,
        "kind": pe.kind,
        "ts": pe.ts,
        "tool": pe.tool,
        "path": pe.path,
        "content": pe.content,
        "tool_input": pe.tool_input,
        "tool_output": pe.output,
        "tokens_input": 0,
        "tokens_output": 0,
        "cost_usd": None,
        "model": None,
        "flags": list(pe.flags or []),
    }


def _iso_opt(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.isoformat()
    return str(dt)


def trace_from_rows(row, events: list) -> dict:
    """Owner/full OTLP trace from a ``Session`` row + its ``Event`` rows."""
    # Model for the conversation root = the model seen on token events, if any.
    model = None
    for e in events:
        raw = e.raw if isinstance(getattr(e, "raw", None), dict) else {}
        if e.kind == "token" and isinstance(raw.get("model"), str):
            model = raw["model"]
            break
    session = {
        "id": row.id,
        "agent": row.agent,
        "user": row.user,
        "model": model,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "flagged": row.flagged,
        "workspace_id": row.workspace_id,
        "git_remote": row.git_remote,
        "git_branch": row.git_branch,
        "retention_expires_at": _iso_opt(getattr(row, "retention_expires_at", None)),
    }
    return build_trace(session, [normalize_row_event(e) for e in events])


def trace_from_public(public_out, *, grade: Optional[str] = None) -> dict:
    """Redacted OTLP trace from a ``PublicSessionOut`` (public path).

    Deliberately omits ``user``/``cwd``/``git_*``/``workspace_id`` (never on the
    public model) so the projection can't reintroduce a field the public JSON
    endpoint already withholds.
    """
    session = {
        "id": public_out.id,
        "agent": public_out.agent,
        "user": None,
        "model": None,
        "started_at": public_out.started_at,
        "ended_at": public_out.ended_at,
        "flagged": public_out.flagged,
        "grade": grade or (public_out.score.grade if public_out.score else None),
    }
    return build_trace(session, [normalize_public_event(e) for e in public_out.events])
