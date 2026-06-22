"""Route-rules endpoints mounted under /me.

Phase D-lite: minimal CRUD for user-scope routing rules so the frontend
can auto-bootstrap rules when a user creates or joins an organization.
Org-scope rule management is deferred to Phase G.

All writes go through the user's own Supabase JWT — RLS enforces ownership
(only `scope='user' AND user_id = auth.uid()` is writable per the
`route_rules_user_*` policies from migrations/route_rules_schema.sql).
"""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store


class RouteRuleIn(BaseModel):
    match_type: Literal["git_remote", "cwd"]
    match_pattern: str = Field(min_length=1, max_length=512)
    target_org_id: Optional[str] = None  # NULL = route to personal
    priority: int = Field(default=100, ge=0, le=10000)
    enabled: bool = True


class RouteRuleOut(BaseModel):
    id: str
    user_id: Optional[str] = None
    org_id: Optional[str] = None
    scope: str
    priority: int
    match_type: str
    match_pattern: str
    target_org_id: Optional[str] = None
    enabled: bool
    created_at: str
    updated_at: str


async def list_route_rules(user_token: str, user_id: UUID) -> list[RouteRuleOut]:
    """Return the caller's user-scope rules + any org-scope rules of orgs
    they belong to. RLS enforces the visibility.
    """
    sb = get_data_store(access_token=user_token)
    try:
        # Owner-scoped — route rules are personal config (was: returned all users').
        rows = sb.query_records(
            "route_rules",
            filters={"user_id": str(user_id)},
            order_by="priority",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list failed: {exc}") from exc
    return [RouteRuleOut(**row) for row in (rows or [])]


async def create_route_rule(
    user_token: str, user_id: UUID, body: RouteRuleIn,
) -> RouteRuleOut:
    """Create a user-scope rule. Supabase RLS will reject any attempt to
    insert a row with `user_id != auth.uid()` or `scope != 'user'`."""
    sb = get_data_store(access_token=user_token)
    payload = {
        "user_id": str(user_id),
        "scope": "user",
        "priority": body.priority,
        "match_type": body.match_type,
        "match_pattern": body.match_pattern,
        "target_org_id": body.target_org_id,
        "enabled": body.enabled,
    }
    try:
        # SupabaseManager lacks a generic insert; call the client directly.
        resp = sb.client.table("route_rules").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"insert failed: {exc}") from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(status_code=500, detail="insert returned no row")
    return RouteRuleOut(**rows[0])


async def delete_route_rule(user_token: str, rule_id: str, user_id: UUID) -> None:
    """Delete a user-scope rule — scoped to the caller (was: any rule by id)."""
    sb = get_data_store(access_token=user_token)
    try:
        resp = (
            sb.client.table("route_rules")
            .delete()
            .eq("id", rule_id)
            .eq("user_id", str(user_id))
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"delete failed: {exc}") from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(status_code=404, detail="rule not found or not yours")
    return None
