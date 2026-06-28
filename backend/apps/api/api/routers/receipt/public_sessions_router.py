"""Public (unauth) session-read endpoint — GTM issue #79.

Serves `GET /api/v1/public/sessions/{id}` for sessions flipped public via
POST /sessions/{id}/share. The endpoint:

- Returns 404 when the session is private OR doesn't exist (same shape for
  both so callers can't probe private sessions by id).
- Redacts owner PII (no `user` field on the wire; no `cwd`, `git_remote`,
  or `git_branch`).
- Redacts the **content** of any event that carries a `secret_*` flag —
  the structural fact of the flag stays visible (that's the story the
  viewer came for) but the raw credential does not. Non-secret flagged
  events (shell_rm, db_destructive, migration_edit, etc.) are returned
  verbatim because the narrative depends on them.
- Rate-limited to 60/min per remote IP when `RATELIMIT_ENABLED=1`. Fail-
  open behavior of `limiter` means a Redis outage doesn't take the public
  surface down.

The route is mounted separately from the authed SessionsRouter so the
`/public/` prefix lives next to the `/sessions/` prefix without either
router knowing about the other. See main.py for mount order.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlmodel import Session as SQLSession
from sqlmodel import select

from apps.api.api.core.ratelimit import limiter

from .db import get_session
from .models import (
    Event,
    PublicEventOut,
    PublicSessionOut,
    ScoreBreakdown,
)
from .models import (
    Session as SessionRow,
)
from .red_flags import _SECRET_PATTERNS
from .scoring import compute_score
from .sessions_router import _enrich_events, _summarize_files_changed

# ── TSU-167 render/read caching ──────────────────────────────────────────────
# A shared session is effectively immutable once the run ends, so the public
# read is safe to cache + content-address. The ETag is derived from the stable
# row fields; a CDN/proxy serves a viral spike from cache and clients revalidate
# cheaply (304) instead of re-running the redaction + scoring per hit. Bump
# _PUBLIC_VIEW_VERSION whenever the public payload SHAPE changes so stale caches
# don't serve an old schema. s-maxage is kept short (60s) so a `yoru share`
# REVOKE takes effect quickly — the public read must not outlive un-sharing.
_PUBLIC_VIEW_VERSION = "1"
_PUBLIC_CACHE_CONTROL = "public, s-maxage=60, stale-while-revalidate=300"


def _public_etag(row: SessionRow) -> str:
    """Weak ETag over the stable, public-visible fields of a shared session."""
    basis = "|".join(
        str(x)
        for x in (
            _PUBLIC_VIEW_VERSION,
            row.id,
            row.ended_at,
            row.tools_count,
            row.files_count,
            row.tokens_input,
            row.tokens_output,
            row.flagged,
            tuple(row.flags or []),
        )
    )
    return 'W/"' + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20] + '"'


def _if_none_match(header: str | None, etag: str) -> bool:
    """True when the client's If-None-Match covers our ETag (→ 304)."""
    if not header:
        return False
    return etag in header or header.strip() == "*"

# ── TSU-44 redaction-pass — defense-in-depth content scrubber ────────────────
# The flag-based strip in _redact_event nulls events the ingest scanner flagged
# secret_*. This scrubber additionally masks credentials/paths/repo identity
# that slipped through UNFLAGGED, applied to every public-facing string (event
# content/output/tool_input/path + session title/summary + file paths) before
# a session is served at /s/:id. Private-by-default + opt-in share gate WHEN a
# session goes public; this gates WHAT leaves with it.

# Absolute home paths leak the OS username + home layout -> mask the prefix.
_POSIX_HOME_RE = re.compile(r"(?:/Users|/home|/root)/[^/\s\"']+")
_WIN_HOME_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+")
# Git remote URLs carry owner/repo identity. Known hosts only (conservative).
_GIT_REMOTE_RE = re.compile(
    r"(?:git@|(?:https?|ssh)://(?:[^@/\s]+@)?)"
    r"(?:github\.com|gitlab\.com|bitbucket\.org|ssh\.dev\.azure\.com|dev\.azure\.com)"
    r"[:/][\w.-]+/[\w.-]+?(?:\.git)?(?=[\s\"')\]]|$)"
)


def _scrub_public_text(text):
    """Mask secrets/tokens, absolute home paths and git-remote URLs in a
    public-facing string. Returns the input unchanged when falsy or non-str."""
    if not isinstance(text, str) or not text:
        return text
    s = text
    for kind, rx in _SECRET_PATTERNS.items():
        s = rx.sub(f"[redacted:{kind.removeprefix('secret_')}]", s)
    s = _POSIX_HOME_RE.sub("~", s)
    s = _WIN_HOME_RE.sub("~", s)
    s = _GIT_REMOTE_RE.sub("[redacted:repo]", s)
    return s


def _scrub_value(obj):
    """Recursively scrub strings inside a JSON-ish value (e.g. tool_input)."""
    if isinstance(obj, str):
        return _scrub_public_text(obj)
    if isinstance(obj, dict):
        return {k: _scrub_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_value(v) for v in obj]
    return obj


def _is_secret_flag(flag: str) -> bool:
    """True when this red-flag category carries credential content we must
    strip before publishing. Anchors the redaction rule in one place so
    adding a new `secret_*` category (secret_mistral etc.) doesn't require
    touching the router."""
    return flag.startswith("secret_")


def _redact_event(ev_out, *, flags: list[str]) -> PublicEventOut:
    """Copy EventOut into PublicEventOut, stripping content/output/tool_input
    when any `secret_*` flag is set on the event. Structural metadata
    (kind, tool, path, ts, duration_ms) is always preserved."""
    has_secret = any(_is_secret_flag(f) for f in (flags or []))
    base = ev_out.model_dump()
    if has_secret:
        base["content"] = None
        base["output"] = None
        base["tool_input"] = None
    else:
        # Defense-in-depth: scrub credentials/paths/repo-names that weren't
        # flagged on this (visible) event before it goes public.
        base["content"] = _scrub_public_text(base.get("content"))
        base["output"] = _scrub_public_text(base.get("output"))
        base["tool_input"] = _scrub_value(base.get("tool_input"))
    # path + group_key (group_key = f"{tool}:{path or content[:40]}") carry
    # absolute paths / leading content on either branch — scrub both always.
    base["path"] = _scrub_public_text(base.get("path"))
    base["group_key"] = _scrub_public_text(base.get("group_key"))
    # PublicEventOut is a strict subset of EventOut — model_validate drops
    # anything not in its schema (e.g. tokens_input/output/cost_usd that
    # we deliberately omit from the public event shape).
    return PublicEventOut.model_validate(base)


class PublicSessionsRouter:
    """Unauthenticated reader for opt-in public sessions."""

    def __init__(self) -> None:
        self.router = APIRouter(
            prefix="/public/sessions", tags=["receipt:public"]
        )
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        # 60/min per remote IP is generous for a share-driven traffic pattern
        # (an HN frontpage share caps around 1-2 req/s sustained; 60/min
        # absorbs the spike without throttling real readers). Goal is only
        # to stop enumeration of session ids, not rate-shape legitimate reads.
        decorated = limiter.limit("60/minute")(self.get_public_session)
        self.router.get(
            "/{session_id}", response_model=PublicSessionOut
        )(decorated)

    def get_public_session(
        self,
        session_id: str,
        request: Request,  # required by slowapi's limit decorator
        response: Response,
        db: SQLSession = Depends(get_session),
    ) -> PublicSessionOut:
        """Return the stripped-down public view of a shared session.

        404 is returned for both "doesn't exist" and "exists but private"
        so callers can't distinguish the two via timing / status.
        """
        row = db.exec(
            select(SessionRow).where(SessionRow.id == session_id)
        ).first()
        if row is None or not row.is_public:
            # Never cache the 404 — a freshly-revoked or never-shared id must
            # not be served from a CDN, and private ids must stay unguessable.
            raise HTTPException(
                status_code=404,
                detail="session not found",
                headers={"Cache-Control": "no-store"},
            )

        # TSU-167: content-address the public read. Compute the ETag from the
        # stable row BEFORE the expensive event/redaction/scoring work, so a
        # revalidating client (If-None-Match) gets a 304 that skips it entirely.
        etag = _public_etag(row)
        if _if_none_match(request.headers.get("if-none-match"), etag):
            return Response(
                status_code=304,
                headers={"ETag": etag, "Cache-Control": _PUBLIC_CACHE_CONTROL},
            )

        # Same event window as authed detail: last 1000 + any flagged rows
        # outside that window. Flagged events carry the narrative; silent
        # drop would defeat the point of the public replay.
        recent = db.exec(
            select(Event)
            .where(Event.session_id == session_id)
            .order_by(Event.ts.desc())
            .limit(1000)
        ).all()
        recent_ids = {e.id for e in recent}
        flagged_extra = db.exec(
            select(Event)
            .where(Event.session_id == session_id)
            .where(Event.flags != [])  # type: ignore[arg-type]
        ).all()
        for ev in flagged_extra:
            if ev.id not in recent_ids and (ev.flags or []):
                recent.append(ev)
                recent_ids.add(ev.id)
        events_asc = sorted(recent, key=lambda e: e.ts)

        events_out = _enrich_events(events_asc)
        events_public = [
            _redact_event(e, flags=list(e.flags or [])) for e in events_out
        ]
        files_out = _summarize_files_changed(events_asc)
        # Scrub absolute home paths out of the public file list.
        for _f in files_out:
            if getattr(_f, "path", None):
                _f.path = _scrub_public_text(_f.path)

        tool_call_count = sum(
            1 for e in events_asc if e.kind in ("tool_use", "file_change")
        )
        error_count = sum(1 for e in events_asc if e.kind == "error")
        score = compute_score(
            files_count=row.files_count,
            tools_called=row.tools_called,
            tokens_output=row.tokens_output,
            tool_call_count=tool_call_count,
            error_count=error_count,
            flags=row.flags,
        )
        score_out = ScoreBreakdown(**asdict(score))

        # Cache the full payload at the edge + enable client revalidation.
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = _PUBLIC_CACHE_CONTROL

        # Explicit field-by-field construction — enumerating every public
        # field here means a future addition to SessionRow doesn't
        # accidentally leak into the public view. PII fields (user, cwd,
        # git_remote, git_branch, workspace_id) are intentionally NOT
        # listed.
        return PublicSessionOut(
            id=row.id,
            agent=row.agent,
            started_at=row.started_at,
            ended_at=row.ended_at,
            tools_count=row.tools_count,
            files_count=row.files_count,
            tokens_input=row.tokens_input,
            tokens_output=row.tokens_output,
            cost_usd=row.cost_usd,
            flagged=row.flagged,
            flags=list(row.flags or []),
            title=_scrub_public_text(row.title),
            files_changed=files_out,
            tools_called=list(row.tools_called or []),
            summary=_scrub_public_text(row.summary),
            events=events_public,
            score=score_out,
        )
