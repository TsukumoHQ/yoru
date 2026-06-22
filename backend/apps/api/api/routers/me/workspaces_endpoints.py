"""Workspace endpoints mounted under /me (Phase W2).

A workspace is where sessions land. Two flavors live in the same table:
  - personal: org_id NULL, owner_user_id = a human
  - team:     org_id set, still tracks owner_user_id (the creator)

All writes go through the user's own Supabase JWT — RLS policies from
workspaces_schema.sql enforce: personal ws editable by owner only;
team ws editable by org admin/owner.

Plan gating (workspaces_max) is enforced in-code here rather than via RLS
because it's a COUNT check that RLS can't cleanly express.
"""
from __future__ import annotations

import os
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field

from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store


# ---------- Schemas ----------

class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=64)
    org_id: Optional[str] = None  # None → personal ws


class WorkspaceOut(BaseModel):
    id: str
    name: str
    slug: str
    org_id: Optional[str] = None
    owner_user_id: str
    settings: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class WorkspacePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    settings: Optional[dict] = None


class WorkspaceRepoIn(BaseModel):
    host: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=128)
    repo: str = Field(min_length=1, max_length=256)


class WorkspaceRepoOut(BaseModel):
    id: str
    workspace_id: str
    host: str
    owner: str
    repo: str
    full_name: str
    created_at: str


class PromoteIn(BaseModel):
    org_name: str = Field(min_length=1, max_length=128)
    org_slug: Optional[str] = Field(default=None, min_length=1, max_length=64)


# ---------- Helpers ----------

def _slugify(s: str) -> str:
    import re
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "workspace"


def _sb(user_token: str) -> SupabaseManager:
    return get_data_store(access_token=user_token)


# ---------- Endpoints ----------

async def list_workspaces(user_token: str, user_id: UUID) -> list[WorkspaceOut]:
    sb = _sb(user_token)
    # Group-scoped visibility: a caller sees their own + group-mates' workspaces;
    # admin sees all. Closes the cross-user leak (was: every user saw all).
    from apps.api.api.services.access.visibility import visible_owner_ids
    allowed = await visible_owner_ids(user_id)
    try:
        resp = (
            sb.client.table("workspaces")
            .select("*")
            .is_("deleted_at", "null")
            .order("created_at")
            .execute()
        )
        rows = getattr(resp, "data", None) or []
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list failed: {exc}") from exc
    if allowed is not None:
        rows = [r for r in rows if str(r.get("owner_user_id")) in allowed]
    out: list[WorkspaceOut] = []
    for r in rows:
        out.append(WorkspaceOut(
            id=r["id"],
            name=r["name"],
            slug=r["slug"],
            org_id=r.get("org_id"),
            owner_user_id=r["owner_user_id"],
            settings=r.get("settings") or {},
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        ))
    return out


async def create_workspace(
    user_token: str, user_id: UUID, body: WorkspaceIn,
) -> WorkspaceOut:
    sb = _sb(user_token)

    # Plan gating — only for personal workspaces (team ws gates are seat-based
    # on the org plan). If body.org_id is None we count the user's perso ws.
    if body.org_id is None:
        _enforce_workspaces_max(sb, str(user_id))

    slug = (body.slug or _slugify(body.name)).strip("-")
    payload = {
        "name": body.name,
        "slug": slug,
        "org_id": body.org_id,
        "owner_user_id": str(user_id),
    }
    try:
        resp = sb.client.table("workspaces").insert(payload).execute()
    except Exception as exc:
        msg = str(exc)
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            raise HTTPException(status_code=409, detail="slug already taken") from exc
        raise HTTPException(status_code=400, detail=f"insert failed: {exc}") from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(status_code=500, detail="insert returned no row")
    r = rows[0]
    return WorkspaceOut(
        id=r["id"], name=r["name"], slug=r["slug"],
        org_id=r.get("org_id"), owner_user_id=r["owner_user_id"],
        settings=r.get("settings") or {},
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


async def update_workspace(
    user_token: str, workspace_id: str, body: WorkspacePatch, user_id: UUID,
) -> WorkspaceOut:
    sb = _sb(user_token)
    patch: dict = {}
    if body.name is not None:
        patch["name"] = body.name
    if body.settings is not None:
        patch["settings"] = body.settings
    if not patch:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        # Owner-only mutation — scope the update to the caller's own row.
        resp = (
            sb.client.table("workspaces")
            .update(patch)
            .eq("id", workspace_id)
            .eq("owner_user_id", str(user_id))
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"update failed: {exc}") from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(status_code=404, detail="workspace not found or not yours")
    r = rows[0]
    return WorkspaceOut(
        id=r["id"], name=r["name"], slug=r["slug"],
        org_id=r.get("org_id"), owner_user_id=r["owner_user_id"],
        settings=r.get("settings") or {},
        created_at=r["created_at"], updated_at=r["updated_at"],
    )


async def delete_workspace(user_token: str, workspace_id: str, user_id: UUID) -> None:
    from datetime import datetime, timezone
    sb = _sb(user_token)
    now = datetime.now(timezone.utc).isoformat()
    try:
        # Owner-only — scope the soft-delete to the caller's own row.
        resp = (
            sb.client.table("workspaces")
            .update({"deleted_at": now})
            .eq("id", workspace_id)
            .eq("owner_user_id", str(user_id))
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"delete failed: {exc}") from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(status_code=404, detail="workspace not found or not yours")
    return None


# ---------- Repo mapping ----------

async def list_workspace_repos(user_token: str, workspace_id: str) -> list[WorkspaceRepoOut]:
    sb = _sb(user_token)
    try:
        rows = sb.query_records(
            "workspace_repos",
            filters={"workspace_id": workspace_id},
            order_by="created_at",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"list repos failed: {exc}") from exc
    return [WorkspaceRepoOut(**r) for r in (rows or [])]


async def add_workspace_repo(
    user_token: str, user_id: UUID, workspace_id: str, body: WorkspaceRepoIn,
) -> WorkspaceRepoOut:
    sb = _sb(user_token)
    payload = {
        "workspace_id": workspace_id,
        "host": body.host,
        "owner": body.owner,
        "repo": body.repo,
        "added_by": str(user_id),
    }
    try:
        resp = sb.client.table("workspace_repos").insert(payload).execute()
    except Exception as exc:
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(
                status_code=409,
                detail=f"repo already mapped: {body.host}/{body.owner}/{body.repo}",
            ) from exc
        raise HTTPException(status_code=400, detail=f"add repo failed: {exc}") from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(status_code=500, detail="insert returned no row")
    return WorkspaceRepoOut(**rows[0])


async def remove_workspace_repo(
    user_token: str, workspace_id: str, repo_id: str,
) -> None:
    sb = _sb(user_token)
    try:
        resp = (
            sb.client.table("workspace_repos")
            .delete()
            .eq("id", repo_id)
            .eq("workspace_id", workspace_id)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"remove repo failed: {exc}") from exc
    rows = getattr(resp, "data", None) or []
    if not rows:
        raise HTTPException(status_code=404, detail="repo mapping not found")
    return None


# ---------- Promote personal workspace → organization ----------

async def promote_workspace(
    user_token: str, user_id: UUID, workspace_id: str, body: PromoteIn,
) -> dict:
    """Promote a personal workspace to a team org.

    Creates a new `organizations` row owned by the user, then moves the
    workspace to belong to that org (sets workspace.org_id). Existing
    sessions already point at the workspace so they follow automatically.
    """
    sb = _sb(user_token)

    # Fetch + verify ownership.
    rows = sb.query_records(
        "workspaces",
        filters={"id": workspace_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="workspace not found")
    ws = rows[0]
    if ws.get("deleted_at"):
        raise HTTPException(status_code=404, detail="workspace is deleted")
    if ws.get("owner_user_id") != str(user_id):
        raise HTTPException(status_code=403, detail="only the owner can promote")
    if ws.get("org_id"):
        raise HTTPException(status_code=400, detail="workspace is already in an organization")

    org_slug = (body.org_slug or _slugify(body.org_name)).strip("-")
    # Create the org. RLS (organizations_insert) requires owner_id = auth.uid().
    try:
        org_resp = sb.client.table("organizations").insert({
            "name": body.org_name,
            "slug": org_slug,
            "type": "team",
            "owner_id": str(user_id),
        }).execute()
    except Exception as exc:
        msg = str(exc).lower()
        if "unique" in msg or "duplicate" in msg:
            raise HTTPException(status_code=409, detail="org slug already taken") from exc
        raise HTTPException(status_code=400, detail=f"promote/create-org failed: {exc}") from exc
    org_rows = getattr(org_resp, "data", None) or []
    if not org_rows:
        raise HTTPException(status_code=500, detail="org insert returned no row")
    new_org = org_rows[0]

    # Add the creator as owner in organization_members (normally handled by a
    # trigger on orgs.create in Yoru; doing it explicitly here in case).
    try:
        sb.client.table("organization_members").insert({
            "org_id": new_org["id"],
            "user_id": str(user_id),
            "role": "owner",
        }).execute()
    except Exception:
        # Idempotent if a trigger already inserted — swallow conflict.
        pass

    # Move the workspace.
    try:
        ws_resp = sb.client.table("workspaces").update({
            "org_id": new_org["id"],
        }).eq("id", workspace_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"move workspace failed: {exc}") from exc
    ws_rows = getattr(ws_resp, "data", None) or []
    if not ws_rows:
        raise HTTPException(status_code=500, detail="workspace move returned no row")

    return {
        "organization": new_org,
        "workspace": ws_rows[0],
    }


# ---------- Plan gating ----------

def _enforce_workspaces_max(sb, user_id: str) -> None:
    """Count the user's personal workspaces, compare to the plan's
    `workspaces_max` feature. 402 if at/over limit.

    Resolution: user_grants → plan_features → features.default_value.
    For simplicity we read the pre-joined `user_subscription_details` view
    (if it exposes feature values) or the features/plan_features tables.

    The value is expected as `{"limit": N}` where -1 or -2 mean unlimited.
    """
    # Self-host (billing disabled) → everything unlimited, no quota gating.
    if os.getenv("BILLING_ENABLED", "false").strip().lower() not in ("1", "true", "yes"):
        return
    # Fetch user's current personal workspace count.
    try:
        res = (
            sb.client.table("workspaces")
            .select("id", count="exact")
            .eq("owner_user_id", user_id)
            .is_("org_id", "null")
            .is_("deleted_at", "null")
            .execute()
        )
    except Exception:
        return  # fail open on transient errors
    count = getattr(res, "count", None)
    if count is None:
        count = len(getattr(res, "data", None) or [])

    # Resolve the workspaces_max value via features/plan/grants.
    limit = _resolve_feature_limit(sb, user_id, "workspaces_max", default=1)
    if limit is not None and limit >= 0 and count >= limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Your plan allows {limit} personal workspace(s). "
                f"Upgrade to create more."
            ),
            headers={"X-Upgrade-Url": "/settings/billing"},
        )


def _resolve_feature_limit(
    sb: SupabaseManager, user_id: str, feature_key: str, default: int = 0,
) -> int | None:
    """Resolve a quota feature's `limit` field in the order:
      1. active user_grants (not expired)
      2. subscription → plan → plan_features
      3. features.default_value

    Returns None if feature doesn't exist (treat as unlimited).
    -1 means unlimited by convention.
    """
    # Fetch the feature id.
    feats = sb.query_records(
        "features",
        filters={"key": feature_key},
    )
    if not feats:
        return None
    feat = feats[0]
    default_limit = _extract_limit(feat.get("default_value"), default)

    # 1. Grant override
    grants = sb.query_records(
        "user_grants",
        filters={
            "user_id": str(user_id),
            "feature_id": feat["id"],
        },
    )
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for g in grants or []:
        exp = g.get("expires_at")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
                if exp_dt < now:
                    continue
            except ValueError:
                pass
        return _extract_limit(g.get("value"), default_limit)

    # 2. Plan → plan_features
    subs = sb.query_records(
        "subscriptions",
        filters={"user_id": str(user_id), "status": "active"},
    )
    if subs:
        plan_id = subs[0].get("plan_id")
        if plan_id:
            pf = sb.query_records(
                "plan_features",
                filters={
                    "plan_id": plan_id,
                    "feature_id": feat["id"],
                },
            )
            if pf:
                return _extract_limit(pf[0].get("value"), default_limit)

    # 3. Default
    return default_limit


def _extract_limit(value, default: int) -> int:
    """Pull `limit` from a JSONB payload like {"limit": N}. Accept bare ints too."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, dict):
        lim = value.get("limit")
        if isinstance(lim, (int, float)):
            return int(lim)
    return default
