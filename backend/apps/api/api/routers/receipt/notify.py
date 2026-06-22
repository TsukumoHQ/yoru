"""Fire-and-forget notification helper for Receipt ingest paths.

Why not use Yoru's NotificationService directly?
- Events ingest authenticates via CLI hook-token (rcpt_*), not a Supabase JWT.
- Inserting into `public.notifications` with RLS needs a user JWT OR the
  service_role key OR a SECURITY DEFINER bypass. We chose the last — the
  `public.notify_user_by_email` RPC resolves email → user_id inside the
  function with elevated privileges, no service_role key in the process.

Fire-and-forget: failures are logged and swallowed. A failed notification
insert must never break event ingest.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# The Supabase URL + anon key are the same env the rest of the app reads.
_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
_SUPABASE_ANON = os.environ.get("SUPABASE_ANON_KEY", "")


def notify_user_by_email(
    email: str,
    type: str,
    title: str,
    message: str,
    action_url: str | None = None,
    metadata: dict[str, Any] | None = None,
    timeout_seconds: float = 2.0,
) -> None:
    """Best-effort notification insert via the `notify_user_by_email` RPC."""
    if not _SUPABASE_URL or not _SUPABASE_ANON:
        _log.debug("notify_user_by_email skipped — Supabase env missing")
        return
    try:
        resp = httpx.post(
            f"{_SUPABASE_URL}/rest/v1/rpc/notify_user_by_email",
            headers={
                "apikey": _SUPABASE_ANON,
                "Authorization": f"Bearer {_SUPABASE_ANON}",
                "Content-Type": "application/json",
            },
            json={
                "p_email": email,
                "p_type": type,
                "p_title": title,
                "p_message": message,
                "p_action_url": action_url,
                "p_metadata": metadata or {},
            },
            timeout=timeout_seconds,
        )
        if resp.status_code >= 400:
            _log.warning(
                "notify_user_by_email failed",
                extra={"status": resp.status_code, "body": resp.text[:200], "email": email},
            )
    except Exception as exc:  # pragma: no cover — ingest must never fail on notify
        _log.warning("notify_user_by_email errored", extra={"error": str(exc), "email": email})
