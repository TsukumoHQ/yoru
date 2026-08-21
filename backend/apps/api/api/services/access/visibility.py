"""Group-scoped visibility — the single source of truth for "who can see/touch
whose data" on a self-hosted, single-org instance.

Model (decided): one instance = one company. Users belong to groups. A user
sees their own data plus their group-mates' data; the instance admin sees
everything. This gives team sharing *and* a confidentiality wall between groups
(e.g. legal matters), without any multi-tenant org plumbing.

`None` returned from the resolvers means "no restriction" (admin / sees all).
A returned set means "restrict to exactly these owners".

Two sanctioned authz primitives, two different questions (DEC-yoru-rbac-
ruling-1, design 325e07f9, review-backend-api §2). Don't add a third:
  - THIS module (`visible_scope_sync`/`visible_emails_sync`) = "which ROWS
    can this caller see" — the per-row read-scoping SSOT, used by every
    session/event/export list-and-detail route.
  - `require_org_admin` (`auth_router.py`) = "may this caller run this
    org-wide aggregate/admin ACTION at all" — a binary gate, not a scope.
A new org-wide or admin route reuses one of these two; if it returns
per-row data, it's a visibility question, if it's an all-or-nothing gate on
an aggregate/admin endpoint, it's a `require_org_admin` question.
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


# ── Multi-tenant org scoping (M3, design 44a3774a §3) ───────────────────────
# The audit tables carry org_id (M1/M2). Reads are walled by tenant: a caller
# sees ONLY sessions whose org_id is in their org set, on TOP of the existing
# within-org email/group visibility above. This is the cross-tenant boundary
# that makes one instance safely host N client-orgs.
#
# Per-org owner/admin visibility (4b22046a follow-up, task b153d228): an
# ``organization_members.role in ('owner','admin')`` caller sees EVERY
# member's rows within that org (not just own+group) — but gets no cross-org
# reach, that stays the studio super-admin's alone (profiles.role=='admin').
# A caller can hold different roles in different orgs (schema allows many
# (org_id, user_id) rows), so the widening is per-org, not caller-wide.

# Sentinel org for legacy/unscoped rows — kept in sync with receipt.models.
_DEFAULT_ORG_ID = "default-org"


def visible_scope_sync(
    caller_email: str | None, org_header: str | None = None
) -> tuple[set[str] | None, set[str] | None, set[str]]:
    """Resolve email-visibility, org-visibility, and per-org "full visibility"
    from a SINGLE ``profiles`` lookup (perf: the list path is query-count-
    budgeted).

    Returns ``(emails, orgs, full_org_ids)``:
    - ``emails``: ``None`` = no email restriction (super-admin). A set
      restricts to exactly those owners — EXCEPT within a ``full_org_ids``
      org, where the caller (org owner/admin) sees every member regardless.
    - ``orgs``: ``None`` = no org restriction (super-admin, all orgs). A set
      restricts to exactly those org_ids.
    - ``full_org_ids``: subset of ``orgs`` where the caller has org
      owner/admin role — within THESE orgs specifically, every member's rows
      are visible regardless of ``emails``. Empty for super-admin (moot:
      ``emails`` is already ``None``) and for a caller with no owner/admin
      org membership.

    Mirrors ``visible_emails_sync`` + ``visible_org_ids_sync`` exactly (plus
    the owner/admin widen), just without the duplicate profile query. Use
    this on the hot read path; the two single-purpose helpers stay for other
    callers that don't need the widen.
    """
    if not caller_email:
        return ({caller_email} if caller_email else set()), {_DEFAULT_ORG_ID}, set()
    store = get_data_store()
    profs = store.query_records("profiles", filters={"email": caller_email})
    prof = profs[0] if profs else None

    if prof and prof.get("role") == "admin":
        # Super-admin: all emails; all orgs (or a validly-owned selected org).
        # full_org_ids is moot here — emails is already unrestricted.
        orgs: set[str] | None = None
        if org_header and _is_org_member(store, str(prof["id"]), org_header):
            orgs = {org_header}
        return None, orgs, set()

    if not prof:
        # CLI-only / no-profile identity → owner-only + default org.
        return {caller_email}, {_DEFAULT_ORG_ID}, set()

    # Regular member: own + group-mates' emails; their org memberships; the
    # subset of those orgs where they hold owner/admin role.
    owner_ids = _co_member_user_ids(store, str(prof["id"]))
    emails: set[str] = {caller_email}
    for uid in owner_ids:
        p = store.get_record("profiles", uid)
        if p and p.get("email"):
            emails.add(p["email"])
    org_ids = _member_org_ids(store, str(prof["id"])) or {_DEFAULT_ORG_ID}
    full_org_ids = _owner_admin_org_ids(store, str(prof["id"])) & org_ids
    return emails, org_ids, full_org_ids


def _owner_admin_org_ids(store, user_id_str: str) -> set[str]:
    """org_ids where the user's ``organization_members.role`` is owner/admin."""
    rows = store.query_records("organization_members", filters={"user_id": user_id_str})
    return {
        str(r["org_id"]) for r in rows
        if r.get("org_id") and r.get("role") in ("owner", "admin")
    }


def session_org_wall_clause(session_row_cls, visible, orgs, full_org_ids):
    """Build the SQLAlchemy filter list for "sessions this caller may read",
    given a NON-None ``orgs`` (callers special-case the super-admin
    "no org restriction" case themselves — there is no clause to build then).

    Centralizes the org/owner-admin/email composition so every read path
    (sessions list, session detail, both export routes) enforces IDENTICAL
    wall logic — this is BOLA-sensitive; one implementation beats N
    independently hand-rolled copies that can quietly drift apart.

    Takes ``session_row_cls`` (duck-typed: needs ``.org_id``/``.user``
    columns) rather than importing ``receipt.models`` here, so this module
    stays storage-agnostic like the rest of it.
    """
    from sqlalchemy import and_, or_

    def _org_clause(ids):
        clause = session_row_cls.org_id.in_(ids)
        if _DEFAULT_ORG_ID in ids:
            clause = or_(clause, session_row_cls.org_id.is_(None))
        return clause

    if not full_org_ids:
        filters = [_org_clause(orgs)]
        if visible is not None:
            filters.append(session_row_cls.user.in_(visible))
        return filters

    # Mixed: full visibility within full_org_ids, email/group-gated in the
    # rest of the caller's orgs (a caller can be owner in org A, plain member
    # in org B — the widen must not leak into B).
    plain_orgs = orgs - full_org_ids
    clauses = [_org_clause(full_org_ids)]
    if plain_orgs:
        plain_clause = _org_clause(plain_orgs)
        if visible is not None:
            plain_clause = and_(plain_clause, session_row_cls.user.in_(visible))
        clauses.append(plain_clause)
    return [or_(*clauses)] if len(clauses) > 1 else clauses


def visible_org_ids_sync(
    caller_email: str | None, org_header: str | None = None
) -> set[str] | None:
    """Org ids the caller may read (the tenant wall). None = super-admin, all
    orgs (or the single org named by a valid ``X-Organization-Id``).

    - super-admin (profiles.role=='admin'): None (all orgs). If ``org_header``
      is given AND the admin is a member of it, narrow to ``{org_header}``.
    - regular caller: their org_ids from ``organization_members`` (joined via
      profile id). A caller with NO membership row (legacy single-org deploy,
      or a CLI-only identity) falls back to ``{_DEFAULT_ORG_ID}`` so existing
      instances keep working — their sessions are all the default org (M1
      backfill).
    """
    if not caller_email:
        return {_DEFAULT_ORG_ID}
    store = get_data_store()
    profs = store.query_records("profiles", filters={"email": caller_email})
    prof = profs[0] if profs else None

    is_super_admin = bool(prof and prof.get("role") == "admin")
    if is_super_admin:
        if org_header and _is_org_member(store, str(prof["id"]), org_header):
            return {org_header}
        return None  # all orgs

    if not prof:
        return {_DEFAULT_ORG_ID}
    org_ids = _member_org_ids(store, str(prof["id"]))
    return org_ids or {_DEFAULT_ORG_ID}


def _member_org_ids(store, user_id_str: str) -> set[str]:
    """org_ids the user is a member of (organization_members)."""
    rows = store.query_records(
        "organization_members", filters={"user_id": user_id_str}
    )
    return {str(r["org_id"]) for r in rows if r.get("org_id")}


def _is_org_member(store, user_id_str: str, org_id: str) -> bool:
    return org_id in _member_org_ids(store, user_id_str)
