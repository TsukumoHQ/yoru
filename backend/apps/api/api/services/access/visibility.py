"""Group-scoped visibility — the single source of truth for "who can see/touch
whose data" on a self-hosted, single-org instance.

Model (decided): one instance = one company. Users belong to groups. A user
sees their own data plus their group-mates' data; the instance admin sees
everything. This gives team sharing *and* a confidentiality wall between groups
(e.g. legal matters), without any multi-tenant org plumbing.

`None` returned from the resolvers means "no restriction" (admin / sees all).
A returned set means "restrict to exactly these owners".
"""
from __future__ import annotations

from uuid import UUID

from apps.api.api.services.auth.provider import get_auth_provider
from libs.datastore import get_data_store


async def is_admin(user_id: UUID) -> bool:
    try:
        return (await get_auth_provider().get_user_role(user_id)) == "admin"
    except Exception:
        return False


def _co_member_user_ids(store, user_id_str: str) -> set[str]:
    """All user_ids sharing at least one group with the caller (incl. self)."""
    memberships = store.query_records(
        "user_group_members", filters={"user_id": user_id_str}
    )
    group_ids = [m["group_id"] for m in memberships if m.get("group_id")]
    ids: set[str] = {user_id_str}
    for gid in group_ids:
        for m in store.query_records("user_group_members", filters={"group_id": gid}):
            if m.get("user_id"):
                ids.add(str(m["user_id"]))
    return ids


async def visible_owner_ids(user_id: UUID) -> set[str] | None:
    """Owner user_ids the caller may see. None = admin (everything)."""
    if await is_admin(user_id):
        return None
    return _co_member_user_ids(get_data_store(), str(user_id))


async def visible_emails(user_id: UUID, caller_email: str | None) -> set[str] | None:
    """Owner *emails* the caller may see (sessions are keyed by email).
    None = admin (everything)."""
    owner_ids = await visible_owner_ids(user_id)
    if owner_ids is None:
        return None
    store = get_data_store()
    emails: set[str] = set()
    if caller_email:
        emails.add(caller_email)
    for uid in owner_ids:
        prof = store.get_record("profiles", uid)
        if prof and prof.get("email"):
            emails.add(prof["email"])
    return emails


def visible_emails_sync(caller_email: str | None) -> set[str] | None:
    """Synchronous email-scoped visibility for the email-keyed session store.

    Resolves the caller's profile (which carries role + id), then their group
    co-members' emails. None = admin (sees all). Used by the sync session
    endpoints, which auth by email (CLI + cookie) rather than user_id.
    """
    if not caller_email:
        return set()
    store = get_data_store()
    profs = store.query_records("profiles", filters={"email": caller_email})
    if not profs:
        # No profile (e.g. CLI-only hook-token identity) → owner-only.
        return {caller_email}
    prof = profs[0]
    if prof.get("role") == "admin":
        return None
    owner_ids = _co_member_user_ids(store, str(prof["id"]))
    emails: set[str] = {caller_email}
    for uid in owner_ids:
        p = store.get_record("profiles", uid)
        if p and p.get("email"):
            emails.add(p["email"])
    return emails


async def can_access_owner(user_id: UUID, owner_user_id: str | None) -> bool:
    """Whether the caller may act on a resource owned by `owner_user_id`."""
    allowed = await visible_owner_ids(user_id)
    if allowed is None:
        return True
    return owner_user_id is not None and str(owner_user_id) in allowed
