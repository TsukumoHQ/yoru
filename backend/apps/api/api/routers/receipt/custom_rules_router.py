"""CRUD for org-defined custom red-flag rules (design trovex:961a5e80, task
569f1d47). Dashboard UI for this surface is an explicit follow-up (out of
scope here, per AC) — these are plain JSON endpoints today.

Org-wall pattern matches M5 (``export_router.export_org_audit``): the path
``org_id`` is authoritative, a member outside their org set gets 404 (never
403 — an invisible tenant must be indistinguishable from a nonexistent one),
the studio super-admin may target any org via ``X-Organization-Id``. There is
no per-org-admin role wired yet anywhere in this codebase (documented gap,
``visibility.py``) — write access here is scoped the same as every other
org-write path: org membership, not a separate admin tier.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session as SQLSession
from sqlmodel import select

from .custom_rules import InvalidRule, invalidate_org_cache, validate_rule
from .db import get_session
from .deps import require_current_user
from .models import CustomRule, CustomRuleIn, CustomRuleOut, CustomRuleUpdate


def _wall(current_user: str, org_id: str, x_organization_id: str | None) -> None:
    from apps.api.api.services.access.visibility import visible_scope_sync
    _visible, orgs = visible_scope_sync(current_user, x_organization_id)
    if orgs is not None and org_id not in orgs:
        raise HTTPException(status_code=404, detail="organization not found")


# CustomRule columns that are NOT NULL. CustomRuleUpdate declares every field
# Optional (so an omitted field means "leave alone"), but that also lets a
# caller send an EXPLICIT null on one of these — model_dump(exclude_unset=True)
# keeps a field that was sent, even as null, so an unguarded setattr(row, f,
# None) would reach sqlite's NOT NULL constraint as an unhandled 500 at
# commit. Reject explicit null on these at the API boundary instead (400).
_NOT_NULLABLE_UPDATE_FIELDS = frozenset({"name", "enabled", "match_type", "pattern", "severity"})


def _out(r: CustomRule) -> CustomRuleOut:
    return CustomRuleOut(
        id=r.id, org_id=r.org_id, name=r.name, enabled=r.enabled,
        kind_filter=r.kind_filter, tool_filter=r.tool_filter,
        match_type=r.match_type, pattern=r.pattern, severity=r.severity,
        created_by=r.created_by, created_at=r.created_at, updated_at=r.updated_at,
    )


class CustomRulesRouter:
    def __init__(self) -> None:
        self.router = APIRouter(tags=["receipt:custom-rules"])
        self.router.get("/orgs/{org_id}/red-flag-rules")(self.list_rules)
        self.router.post("/orgs/{org_id}/red-flag-rules", status_code=201)(self.create_rule)
        self.router.patch("/orgs/{org_id}/red-flag-rules/{rule_id}")(self.update_rule)
        self.router.delete(
            "/orgs/{org_id}/red-flag-rules/{rule_id}",
            status_code=204, response_model=None,
        )(self.delete_rule)

    def get_router(self) -> APIRouter:
        return self.router

    def list_rules(
        self,
        org_id: str,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
        x_organization_id: str | None = Header(None, alias="X-Organization-Id"),
    ) -> list[CustomRuleOut]:
        _wall(current_user, org_id, x_organization_id)
        rows = db.exec(
            select(CustomRule).where(CustomRule.org_id == org_id)
            .order_by(CustomRule.created_at.asc())
        ).all()
        return [_out(r) for r in rows]

    def create_rule(
        self,
        org_id: str,
        body: CustomRuleIn,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
        x_organization_id: str | None = Header(None, alias="X-Organization-Id"),
    ) -> CustomRuleOut:
        _wall(current_user, org_id, x_organization_id)
        try:
            validate_rule(
                org_id=org_id, match_type=body.match_type,
                pattern=body.pattern, severity=body.severity, session=db,
            )
        except InvalidRule as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = CustomRule(
            org_id=org_id, name=body.name, enabled=body.enabled,
            kind_filter=body.kind_filter, tool_filter=body.tool_filter,
            match_type=body.match_type, pattern=body.pattern,
            severity=body.severity, created_by=current_user,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        invalidate_org_cache(org_id)
        return _out(row)

    def update_rule(
        self,
        org_id: str,
        rule_id: str,
        body: CustomRuleUpdate,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
        x_organization_id: str | None = Header(None, alias="X-Organization-Id"),
    ) -> CustomRuleOut:
        _wall(current_user, org_id, x_organization_id)
        row = db.get(CustomRule, rule_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status_code=404, detail="rule not found")
        updates = body.model_dump(exclude_unset=True)
        null_required = [
            f for f in _NOT_NULLABLE_UPDATE_FIELDS if f in updates and updates[f] is None
        ]
        if null_required:
            raise HTTPException(
                status_code=400,
                detail=f"cannot set to null: {', '.join(sorted(null_required))}",
            )
        new_match_type = updates.get("match_type", row.match_type)
        new_pattern = updates.get("pattern", row.pattern)
        new_severity = updates.get("severity", row.severity)
        if {"match_type", "pattern", "severity"} & updates.keys():
            try:
                validate_rule(
                    org_id=org_id, match_type=new_match_type,
                    pattern=new_pattern, severity=new_severity, session=db,
                    enforce_cap=False,
                )
            except InvalidRule as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        for field, value in updates.items():
            setattr(row, field, value)
        row.updated_at = datetime.now(UTC)
        db.add(row)
        db.commit()
        db.refresh(row)
        invalidate_org_cache(org_id)
        return _out(row)

    def delete_rule(
        self,
        org_id: str,
        rule_id: str,
        db: SQLSession = Depends(get_session),
        current_user: str = Depends(require_current_user),
        x_organization_id: str | None = Header(None, alias="X-Organization-Id"),
    ) -> None:
        _wall(current_user, org_id, x_organization_id)
        row = db.get(CustomRule, rule_id)
        if row is None or row.org_id != org_id:
            raise HTTPException(status_code=404, detail="rule not found")
        db.delete(row)
        db.commit()
        invalidate_org_cache(org_id)
