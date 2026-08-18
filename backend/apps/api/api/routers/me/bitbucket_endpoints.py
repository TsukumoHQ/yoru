"""Bitbucket integration endpoints — the Bitbucket-Cloud twin of
github_endpoints.py.

Flow (mirrors GitHub):
  1. Frontend redirects the user to
     `{SUPABASE_URL}/auth/v1/authorize?provider=bitbucket
     &redirect_to={FRONTEND_ORIGIN}/integrations/bitbucket/callback`.
  2. After Bitbucket approves, Supabase returns an access_token and
     `provider_token` (the raw Bitbucket OAuth token) in the URL hash.
  3. The callback page POSTs `{provider_token}` to `/me/bitbucket/connect`
     with the existing dashboard cookie session.
  4. Backend validates it against Bitbucket's API and persists it in
     `bitbucket_integrations`.
  5. Frontend calls `/me/bitbucket/repos`, then `/me/bitbucket/auto-route`
     to bulk-assign repos to a workspace (host `bitbucket.org`).

The persisted row lives in `bitbucket_integrations`, the exact shape of
`github_integrations`. On the self-hosted default (local doc-store) that table
is schemaless and needs no migration; the Supabase path needs the mirrored
table + RLS created operator-side (see CTO — parallel to github_integrations).

Bitbucket's Supabase provider token, like GitHub's, has a short TTL — when it
expires the user sees "Reconnect Bitbucket" in the UI. Bitbucket API 2.0 IDs
are opaque UUID strings (not ints), so `BitbucketRepoOut.id` is a `str`.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field

from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store

_API = "https://api.bitbucket.org/2.0"
_HOST = "bitbucket.org"


class BitbucketConnectIn(BaseModel):
    provider_token: str = Field(min_length=20)
    scopes: Optional[list[str]] = None


class BitbucketStatusOut(BaseModel):
    connected: bool
    bitbucket_username: Optional[str] = None
    connected_at: Optional[str] = None
    expires_at: Optional[str] = None


class BitbucketRepoOut(BaseModel):
    id: str
    full_name: str
    owner: str
    repo: str
    host: str = _HOST
    private: bool
    updated_at: Optional[str] = None
    mapped_workspace_id: Optional[str] = None


class AutoRouteIn(BaseModel):
    workspace_id: str
    # List of "workspace/repo" strings the frontend got from /me/bitbucket/repos.
    repos: list[str] = Field(min_length=1, max_length=500)


def _sb(user_token: str) -> SupabaseManager:
    return get_data_store(access_token=user_token)


async def get_bitbucket_status(
    user_token: str, user_id: UUID,
) -> BitbucketStatusOut:
    sb = _sb(user_token)
    rows = sb.query_records(
        "bitbucket_integrations",
        filters={"user_id": str(user_id)},
    )
    if not rows:
        return BitbucketStatusOut(connected=False)
    row = rows[0]
    return BitbucketStatusOut(
        connected=True,
        bitbucket_username=row.get("bitbucket_username"),
        connected_at=row.get("connected_at"),
        expires_at=row.get("expires_at"),
    )


async def connect_bitbucket(
    user_token: str, user_id: UUID, body: BitbucketConnectIn,
) -> BitbucketStatusOut:
    """Validate the provider_token by hitting GET /2.0/user, then persist."""
    try:
        resp = httpx.get(
            f"{_API}/user",
            headers={
                "Authorization": f"Bearer {body.provider_token}",
                "Accept": "application/json",
            },
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"bitbucket unreachable: {exc}"
        ) from exc
    if resp.status_code == 401:
        raise HTTPException(
            status_code=401, detail="provider token rejected by Bitbucket"
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"bitbucket status {resp.status_code}: {resp.text[:200]}",
        )

    bb_user = resp.json()
    bitbucket_uuid = bb_user.get("uuid")
    bitbucket_username = bb_user.get("username") or bb_user.get("nickname")

    sb = _sb(user_token)
    payload = {
        "user_id": str(user_id),
        "provider_token": body.provider_token,
        "bitbucket_uuid": bitbucket_uuid,
        "bitbucket_username": bitbucket_username,
        "scopes": body.scopes or [],
    }
    try:
        sb.client.table("bitbucket_integrations").upsert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"persist failed: {exc}") from exc

    return BitbucketStatusOut(
        connected=True,
        bitbucket_username=bitbucket_username,
        connected_at=None,
        expires_at=None,
    )


async def disconnect_bitbucket(user_token: str, user_id: UUID) -> None:
    sb = _sb(user_token)
    try:
        sb.client.table("bitbucket_integrations").delete().eq(
            "user_id", str(user_id)
        ).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"disconnect failed: {exc}"
        ) from exc
    return None


async def list_bitbucket_repos(
    user_token: str, user_id: UUID, per_page: int = 100, page: int = 1,
) -> list[BitbucketRepoOut]:
    """List the user's repos, merging in workspace_id for already-mapped ones."""
    sb = _sb(user_token)
    rows = sb.query_records(
        "bitbucket_integrations",
        filters={"user_id": str(user_id)},
    )
    if not rows:
        raise HTTPException(status_code=400, detail="Bitbucket not connected")
    provider_token = rows[0]["provider_token"]

    try:
        resp = httpx.get(
            f"{_API}/repositories",
            headers={
                "Authorization": f"Bearer {provider_token}",
                "Accept": "application/json",
            },
            params={
                "role": "member",
                "pagelen": min(max(per_page, 1), 100),
                "page": max(page, 1),
                "sort": "-updated_on",
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"bitbucket unreachable: {exc}"
        ) from exc
    if resp.status_code == 401:
        raise HTTPException(
            status_code=401,
            detail="Bitbucket token expired — reconnect Bitbucket in settings",
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"bitbucket status {resp.status_code}: {resp.text[:200]}",
        )

    raw = (resp.json() or {}).get("values", []) or []

    # Look up existing workspace_repos rows to mark already-mapped repos.
    mapped_rows = []
    try:
        mapped_resp = (
            sb.client.table("workspace_repos")
            .select("workspace_id,host,owner,repo")
            .eq("host", _HOST)
            .execute()
        )
        mapped_rows = getattr(mapped_resp, "data", None) or []
    except Exception:
        pass
    mapped_by_key = {
        f"{m['owner']}/{m['repo']}": m["workspace_id"]
        for m in mapped_rows
    }

    out: list[BitbucketRepoOut] = []
    for r in raw:
        # Bitbucket full_name is "{workspace}/{repo_slug}"; owner = workspace.
        full = r.get("full_name") or ""
        name = r.get("name") or (full.split("/", 1)[1] if "/" in full else full)
        owner = full.split("/", 1)[0] if "/" in full else ""
        out.append(BitbucketRepoOut(
            id=str(r.get("uuid") or full),
            full_name=full or f"{owner}/{name}",
            owner=owner,
            repo=name,
            host=_HOST,
            private=bool(r.get("is_private")),
            updated_at=r.get("updated_on"),
            mapped_workspace_id=mapped_by_key.get(full),
        ))
    return out


async def auto_route_repos(
    user_token: str, user_id: UUID, body: AutoRouteIn,
) -> dict:
    """Bulk-assign a list of Bitbucket repos to a workspace.

    Idempotent: if a repo is already mapped to ANOTHER workspace, we skip it
    (UNIQUE constraint on workspace_repos). Returns counts
    {added, skipped_already_mapped, errors}.
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
                "host": _HOST,
                "owner": owner,
                "repo": repo,
                "added_by": str(user_id),
                # Generated column in Postgres; set explicitly for the local store.
                "full_name": f"{owner}/{repo}",
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
