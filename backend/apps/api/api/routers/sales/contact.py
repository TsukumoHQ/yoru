"""POST /api/v1/sales/contact — public contact-sales form endpoint.

Unauthenticated: a prospect may not have a Yoru account yet. Protected by
slowapi rate limit + honeypot field + minimal input validation. On accept,
writes one row to public.sales_leads via the Supabase service role and
fires a notification email to sales@yoru.sh via Resend.

Honeypot: the form renders an invisible `website_url` input. Bots fill
every text field; humans (keyboard/mouse) skip the hidden one. If it's
non-empty we return 200 OK without inserting — the bot thinks it worked
and goes away, the lead list stays clean.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from apps.api.api.core.ratelimit import limiter
from libs.email.email_manager import EmailManager
from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store

_SALES_EMAIL = os.environ.get("SALES_NOTIFY_EMAIL", "sales@yoru.sh")


class ContactRequest(BaseModel):
    email: EmailStr
    company: str = Field(min_length=1, max_length=200)
    seats_estimate: Optional[str] = Field(default=None, max_length=100)
    use_case: Optional[str] = Field(default=None, max_length=4000)
    source: str = Field(default="marketing", pattern=r"^(marketing|dashboard)$")
    # Honeypot — real users leave this empty; bots fill every text input.
    website_url: Optional[str] = Field(default=None, max_length=500)


class ContactResponse(BaseModel):
    ok: bool = True


def _service_role_supabase() -> SupabaseManager:
    """Bypass-RLS client for inserting leads from an unauth endpoint.

    SupabaseManager defaults to service_role since issue #48; this helper
    just disables cache for the one-shot insert.
    """
    return get_data_store(enable_cache=False)


def _hash_ip(ip: Optional[str]) -> Optional[str]:
    if not ip:
        return None
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


class SalesContactRouter:
    """Mount under `/api/v1/sales` — exposes POST /contact."""

    def __init__(self) -> None:
        self.router = APIRouter(prefix="/sales", tags=["sales:contact"])
        self.logger = LoggingController(app_name="SalesContactRouter")
        self._setup_routes()

    def get_router(self) -> APIRouter:
        return self.router

    def _setup_routes(self) -> None:
        # 5 legitimate submissions per 10 minutes per IP is generous; anything
        # above that from the same IP is almost certainly a bot scripted past
        # the honeypot. slowapi returns 429 automatically.
        decorated = limiter.limit("5/10minutes")(self.contact)
        self.router.post(
            "/contact",
            response_model=ContactResponse,
            status_code=status.HTTP_200_OK,
        )(decorated)

    async def contact(self, request: Request, body: ContactRequest) -> ContactResponse:
        # Honeypot triggered → acknowledge without doing anything. The bot is
        # happy and never learns there's a validation layer worth fuzzing.
        if body.website_url:
            self.logger.log_warning(
                "Sales contact honeypot tripped — dropping submission silently",
                {"email": body.email, "source": body.source},
            )
            return ContactResponse()

        client_ip = (
            request.headers.get("fly-client-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else None)
        )

        row = {
            "email": body.email,
            "company": body.company,
            "seats_estimate": body.seats_estimate,
            "use_case": body.use_case,
            "source": body.source,
            "referrer_url": request.headers.get("referer"),
            "user_agent": request.headers.get("user-agent"),
            "ip_hash": _hash_ip(client_ip),
        }

        try:
            sb = _service_role_supabase()
            sb.client.table("sales_leads").insert(row).execute()
        except Exception as exc:
            self.logger.log_exception(exc, {"operation": "insert_sales_lead"})
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Couldn't record your message — please email sales@yoru.sh directly.",
            ) from exc

        # Fire-and-forget email so a Resend outage can't make the form look
        # broken to the user. If the email fails, we still have the DB row.
        try:
            email = EmailManager()
            subject = f"[Yoru sales] {body.company} — {body.email}"
            html = _render_notification_html(body, client_ip)
            await email.send_simple(
                to_email=_SALES_EMAIL,
                subject=subject,
                html_body=html,
                reply_to=body.email,
            )
        except Exception as exc:
            self.logger.log_warning(
                "Sales lead row saved but notification email failed",
                {"error": str(exc), "email": body.email},
            )

        return ContactResponse()


def _render_notification_html(body: ContactRequest, ip: Optional[str]) -> str:
    use_case = (body.use_case or "(no message)").replace("\n", "<br>")
    return f"""
<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;color:#1a1a1a;">
  <h2 style="margin:0 0 16px;">New sales lead</h2>
  <table cellpadding="6" style="border-collapse:collapse;">
    <tr><td><b>Email</b></td><td><a href="mailto:{body.email}">{body.email}</a></td></tr>
    <tr><td><b>Company</b></td><td>{body.company}</td></tr>
    <tr><td><b>Seats</b></td><td>{body.seats_estimate or '—'}</td></tr>
    <tr><td><b>Source</b></td><td>{body.source}</td></tr>
    <tr><td valign="top"><b>Message</b></td><td>{use_case}</td></tr>
  </table>
  <p style="color:#666;font-size:12px;margin-top:24px;">
    IP (hashed): {_hash_ip(ip) or '—'} ·
    Saved to sales_leads · Reply directly to reach the prospect.
  </p>
</div>
"""
