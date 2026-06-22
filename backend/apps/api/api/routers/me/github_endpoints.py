"""GitHub integration endpoints (Phase W3).

Flow:
  1. Frontend redirects user to `{SUPABASE_URL}/auth/v1/authorize?provider=github
     &redirect_to={FRONTEND_ORIGIN}/integrations/github/callback`.
  2. After GitHub approves, Supabase sends the user back with an access_token
     and `provider_token` (the raw GitHub OAuth token) in the URL hash.
  3. Our callback page reads the hash and POSTs `{provider_token, github_login}`
     to `/me/github/connect` with the existing dashboard cookie session.
  4. Backend persists it in `github_integrations`.
  5. Frontend calls `/me/github/repos` to list the user's repos, then
     `/me/github/auto-route` to bulk-assign repos to a workspace.

The GitHub provider token from Supabase has a 1h TTL — when it expires the
user sees "Reconnect GitHub" in the UI. A GitHub App installation (Phase G)
would give us refresh tokens; the Supabase provider flow doesn't.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store


class GithubConnectIn(BaseModel):
    provider_token: str = Field(min_length=20)
    scopes: Optional[list[str]] = None


class GithubStatusOut(BaseModel):
    connected: bool
    github_login: Optional[str] = None
    connected_at: Optional[str] = None
    expires_at: Optional[str] = None


class GithubRepoOut(BaseModel):
    id: int
    full_name: str
    owner: str
    repo: str
    host: str = "github.com"
    private: bool
    updated_at: Optional[str] = None
    mapped_workspace_id: Optional[str] = None


class AutoRouteIn(BaseModel):
    workspace_id: str
    # List of "owner/repo" strings the frontend got from /me/github/repos.
    repos: list[str] = Field(min_length=1, max_length=500)


def _sb(user_token: str) -> SupabaseManager:
    return get_data_store(access_token=user_token)


async def get_github_status(
    user_token: str, user_id: UUID,
) -> GithubStatusOut:
    sb = _sb(user_token)
    rows = sb.query_records(
        "github_integrations",
        filters={"user_id": str(user_id)},
    )
    if not rows:
        return GithubStatusOut(connected=False)
    row = rows[0]
    return GithubStatusOut(
        connected=True,
        github_login=row.get("github_login"),
        connected_at=row.get("connected_at"),
        expires_at=row.get("expires_at"),
    )


async def connect_github(
    user_token: str, user_id: UUID, body: GithubConnectIn,
) -> GithubStatusOut:
    """Validate the provider_token by hitting GET /user, then persist."""
    try:
        resp = httpx.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {body.provider_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"github unreachable: {exc}") from exc
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="provider token rejected by GitHub")
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"github status {resp.status_code}: {resp.text[:200]}")

    gh_user = resp.json()
    github_user_id = gh_user.get("id")
    github_login = gh_user.get("login")

    sb = _sb(user_token)
    payload = {
        "user_id": str(user_id),
        "provider_token": body.provider_token,
        "github_user_id": github_user_id,
        "github_login": github_login,
        "scopes": body.scopes or [],
    }
    try:
        sb.client.table("github_integrations").upsert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"persist failed: {exc}") from exc

    return GithubStatusOut(
        connected=True,
        github_login=github_login,
        connected_at=None,
        expires_at=None,
    )


async def disconnect_github(user_token: str, user_id: UUID) -> None:
    sb = _sb(user_token)
    try:
        sb.client.table("github_integrations").delete().eq("user_id", str(user_id)).execute()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"disconnect failed: {exc}") from exc
    return None


async def list_github_repos(
    user_token: str, user_id: UUID, per_page: int = 100, page: int = 1,
) -> list[GithubRepoOut]:
    """List the user's repos, merging in workspace_id for already-mapped ones."""
    sb = _sb(user_token)
    rows = sb.query_records(
        "github_integrations",
        filters={"user_id": str(user_id)},
    )
    if not rows:
        raise HTTPException(status_code=400, detail="GitHub not connected")
    provider_token = rows[0]["provider_token"]

    try:
        resp = httpx.get(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"Bearer {provider_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params={
                "per_page": min(max(per_page, 1), 100),
                "page": max(page, 1),
                "sort": "updated",
                "direction": "desc",
                "visibility": "all",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"github unreachable: {exc}") from exc
    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="GitHub token expired — reconnect GitHub in settings",
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"github status {resp.status_code}: {resp.text[:200]}")

    raw = resp.json() or []

    # Look up existing workspace_repos rows to mark already-mapped repos.
    mapped_rows = []
    try:
        mapped_resp = (
            sb.client.table("workspace_repos")
            .select("workspace_id,host,owner,repo")
            .eq("host", "github.com")
            .execute()
        )
        mapped_rows = getattr(mapped_resp, "data", None) or []
    except Exception:
        pass
    mapped_by_key = {
        f"{m['owner']}/{m['repo']}": m["workspace_id"]
        for m in mapped_rows
    }

    out: list[GithubRepoOut] = []
    for r in raw:
        owner_login = (r.get("owner") or {}).get("login", "")
        name = r.get("name", "")
        full = r.get("full_name") or f"{owner_login}/{name}"
        out.append(GithubRepoOut(
            id=r["id"],
            full_name=full,
            owner=owner_login,
            repo=name,
            host="github.com",
            private=bool(r.get("private")),
            updated_at=r.get("updated_at"),
            mapped_workspace_id=mapped_by_key.get(full),
        ))
    return out


async def auto_route_repos(
    user_token: str, user_id: UUID, body: AutoRouteIn,
) -> dict:
    """Bulk-assign a list of GitHub repos to a workspace.

    Idempotent: if a repo is already mapped to ANOTHER workspace, we skip it
    (a repo can only live in one workspace per workspace_repos UNIQUE constraint).
    Returns counts {added, skipped_already_mapped, skipped_errors}.
    """
    sb = _sb(user_token)

    # Sanity: workspace must be visible via RLS.
    ws_rows = sb.query_records(
        "workspaces",
        filters={"id": body.workspace_id},
    )
    if not ws_rows:
        raise HTTPException(status_code=404, detail="workspace not found or not yours")

    added = 0
    already_mapped = 0
    errors = 0
    for full in body.repos:
        if "/" not in full:
            errors += 1
            continue
        owner, repo = full.split("/", 1)
        try:
            sb.client.table("workspace_repos").insert({
                "workspace_id": body.workspace_id,
                "host": "github.com",
                "owner": owner,
                "repo": repo,
                "added_by": str(user_id),
            }).execute()
            added += 1
        except Exception as exc:
            msg = str(exc).lower()
            if "unique" in msg or "duplicate" in msg:
                already_mapped += 1
            else:
                errors += 1
    return {
        "added": added,
        "skipped_already_mapped": already_mapped,
        "errors": errors,
    }
