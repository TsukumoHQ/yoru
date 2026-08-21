"""Regression guard for the authz-primitives documentation (bdb6fe9d,
DEC-yoru-rbac-ruling-1 Q3, review-backend-api §2).

Not a behavioral test — `require_org_admin`'s CLI-bearer-never-authorizes
behavior is exercised directly in test_require_org_admin.py. This one
guards the DOCUMENTATION itself: if a future edit strips the two-primitives
cross-reference or the CliToken-never-a-role invariant from either
docstring, this catches the silent drift rather than letting the written
rule quietly rot out of sync with the code it describes.
"""
from __future__ import annotations

from apps.api.api.routers.receipt import auth_router
from apps.api.api.services.access import visibility


def test_visibility_module_documents_the_two_authz_primitives():
    doc = visibility.__doc__ or ""
    assert "require_org_admin" in doc
    assert "visible_scope_sync" in doc
    assert "add a third" in doc


def test_require_org_admin_documents_the_clitoken_never_role_invariant():
    doc = auth_router.require_org_admin.__doc__ or ""
    assert "CliToken" in doc
    assert "never a role" in doc
    assert "require_dashboard_jwt" in doc
