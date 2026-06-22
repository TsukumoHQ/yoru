"""Organization service for multi-tenancy system."""

from __future__ import annotations

import math
import os
import re
import secrets
from datetime import datetime, timezone
from uuid import UUID

from libs.email import EmailManager
from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import (
    ConflictError,
    NotFoundError,
    PermissionError,
    ValidationError,
)
from apps.api.api.models.organization.organization_models import (
    OrganizationCreate,
    OrganizationDetailResponse,
    OrganizationInvitationCreate,
    OrganizationInvitationPublicResponse,
    OrganizationInvitationResponse,
    OrganizationListResponse,
    OrganizationMemberAdd,
    OrganizationMemberResponse,
    OrganizationMemberUpdate,
    OrganizationResponse,
    OrganizationRole,
    OrganizationType,
    OrganizationUpdate,
)


class OrganizationService:
    """Service for handling organization operations."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
        email_manager: EmailManager | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="OrganizationService")
        self.email_manager = email_manager or EmailManager()

    # =============================================
    # Organization CRUD Operations
    # =============================================

    async def create_organization(
        self,
        data: OrganizationCreate,
        owner_id: UUID,
        correlation_id: str,
    ) -> OrganizationResponse:
        """
        Create a new organization and add owner as member.

        Args:
            data: Organization creation data
            owner_id: ID of the user creating the organization
            correlation_id: Request correlation ID

        Returns:
            Created organization

        Raises:
            ConflictError: If slug already exists
        """
        context = {
            "operation": "create_organization",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_name": data.name,
            "org_type": data.type.value,
        }
        self.logger.log_info("Creating organization", context)

        try:
            # Generate unique slug
            slug = self._generate_slug(data.name, owner_id)

            # Check if slug already exists
            existing = self.supabase.query_records(
                "organizations",
                filters={"slug": slug},
                correlation_id=correlation_id,
            )
            if existing:
                raise ConflictError(
                    f"Organization with slug '{slug}' already exists",
                    correlation_id,
                )

            # Create organization
            org_data = {
                "name": data.name,
                "slug": slug,
                "type": data.type.value,
                "owner_id": str(owner_id),
                "avatar_url": data.avatar_url,
                "settings": data.settings,
            }
            result = self.supabase.insert_record(
                "organizations", org_data, correlation_id=correlation_id
            )

            # Add owner as member with owner role
            member_data = {
                "org_id": result["id"],
                "user_id": str(owner_id),
                "role": OrganizationRole.OWNER.value,
            }
            self.supabase.insert_record(
                "organization_members", member_data, correlation_id=correlation_id
            )

            # Build response
            result["member_count"] = 1
            result["current_user_role"] = OrganizationRole.OWNER

            self.logger.log_info(
                "Organization created successfully",
                {**context, "org_id": result["id"], "slug": slug},
            )
            return OrganizationResponse(**result)
        except ConflictError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create organization", context)
            raise

    async def create_personal_organization(
        self,
        owner_id: UUID,
        correlation_id: str,
    ) -> OrganizationResponse:
        """
        Create a personal organization for a user.
        Called during signup to create the hidden personal org.

        Args:
            owner_id: ID of the user
            correlation_id: Request correlation ID

        Returns:
            Created personal organization
        """
        context = {
            "operation": "create_personal_organization",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "owner_id": str(owner_id),
        }
        self.logger.log_info("Creating personal organization", context)

        try:
            # Check if user already has a personal org
            existing = self.supabase.query_records(
                "organizations",
                filters={"owner_id": str(owner_id), "type": "personal"},
                correlation_id=correlation_id,
            )
            if existing:
                self.logger.log_info(
                    "Personal org already exists",
                    {**context, "org_id": existing[0]["id"]},
                )
                existing[0]["member_count"] = 1
                existing[0]["current_user_role"] = OrganizationRole.OWNER
                return OrganizationResponse(**existing[0])

            # Create personal organization
            data = OrganizationCreate(
                name="Personal",
                type=OrganizationType.PERSONAL,
            )
            return await self.create_organization(data, owner_id, correlation_id)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create personal organization", context)
            raise

    async def get_organization(
        self,
        org_id: UUID,
        user_id: UUID,
        correlation_id: str,
    ) -> OrganizationResponse:
        """
        Get organization by ID if user is a member.

        Args:
            org_id: Organization ID
            user_id: Current user ID
            correlation_id: Request correlation ID

        Returns:
            Organization

        Raises:
            NotFoundError: If organization not found
            PermissionError: If user is not a member
        """
        context = {
            "operation": "get_organization",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Getting organization", context)

        try:
            # Get organization
            result = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not result or result.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check membership
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership:
                raise PermissionError(
                    "You are not a member of this organization", correlation_id
                )

            # Get member count
            members = self.supabase.query_records(
                "organization_members",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )
            result["member_count"] = len(members)
            result["current_user_role"] = OrganizationRole(membership["role"])

            return OrganizationResponse(**result)
        except (NotFoundError, PermissionError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get organization", context)
            raise

    async def get_organization_detail(
        self,
        org_id: UUID,
        user_id: UUID,
        correlation_id: str,
    ) -> OrganizationDetailResponse:
        """Get organization with members and pending invitations."""
        context = {
            "operation": "get_organization_detail",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Getting organization detail", context)

        try:
            # Get base organization
            org = await self.get_organization(org_id, user_id, correlation_id)

            # Get members
            members = await self.list_members(org_id, user_id, correlation_id)

            # Get pending invitations (only if admin/owner)
            invitations = []
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if membership and membership["role"] in ("owner", "admin"):
                invitations = await self.list_invitations(
                    org_id, user_id, correlation_id
                )

            # Build response
            org_dict = org.model_dump()
            org_dict["members"] = [m.model_dump() for m in members]
            org_dict["pending_invitations"] = [i.model_dump() for i in invitations]

            return OrganizationDetailResponse(**org_dict)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get organization detail", context)
            raise

    async def list_organizations(
        self,
        user_id: UUID,
        correlation_id: str,
        page: int = 1,
        page_size: int = 50,
        include_personal: bool = True,
    ) -> OrganizationListResponse:
        """
        List all organizations the user is a member of.

        Args:
            user_id: Current user ID
            correlation_id: Request correlation ID
            page: Page number
            page_size: Page size
            include_personal: Whether to include personal orgs

        Returns:
            Paginated list of organizations
        """
        context = {
            "operation": "list_organizations",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "page": page,
            "page_size": page_size,
        }
        self.logger.log_info("Listing organizations", context)

        try:
            # Get user's memberships
            memberships = self.supabase.query_records(
                "organization_members",
                filters={"user_id": str(user_id)},
                correlation_id=correlation_id,
            )

            if not memberships:
                return OrganizationListResponse(
                    items=[],
                    total=0,
                    page=page,
                    page_size=page_size,
                    total_pages=1,
                )

            # Fetch organization details for each membership
            all_orgs = []
            for membership in memberships:
                org = self.supabase.get_record(
                    "organizations",
                    membership["org_id"],
                    correlation_id=correlation_id,
                )
                if org and not org.get("deleted_at"):
                    # Filter personal orgs if requested
                    if not include_personal and org["type"] == "personal":
                        continue

                    # Get member count
                    members = self.supabase.query_records(
                        "organization_members",
                        filters={"org_id": org["id"]},
                        correlation_id=correlation_id,
                    )
                    org["member_count"] = len(members)
                    org["current_user_role"] = OrganizationRole(membership["role"])
                    all_orgs.append(org)

            # Sort by created_at descending
            all_orgs.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            # Calculate pagination
            total = len(all_orgs)
            total_pages = math.ceil(total / page_size) if total > 0 else 1
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_orgs = all_orgs[start_idx:end_idx]

            org_responses = [OrganizationResponse(**org) for org in page_orgs]

            return OrganizationListResponse(
                items=org_responses,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            )
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list organizations", context)
            raise

    async def update_organization(
        self,
        org_id: UUID,
        data: OrganizationUpdate,
        user_id: UUID,
        correlation_id: str,
    ) -> OrganizationResponse:
        """
        Update organization. Requires admin or owner role.

        Args:
            org_id: Organization ID
            data: Update data
            user_id: Current user ID
            correlation_id: Request correlation ID

        Returns:
            Updated organization

        Raises:
            NotFoundError: If organization not found
            PermissionError: If user lacks permission
        """
        context = {
            "operation": "update_organization",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Updating organization", context)

        try:
            # Check organization exists and get current data
            existing = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not existing or existing.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check permission
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership or membership["role"] not in ("owner", "admin"):
                raise PermissionError(
                    "Admin or owner role required to update organization",
                    correlation_id,
                )

            # Update organization
            update_data = data.model_dump(exclude_unset=True)
            result = self.supabase.update_record(
                "organizations",
                str(org_id),
                update_data,
                correlation_id=correlation_id,
            )

            # Add member count
            members = self.supabase.query_records(
                "organization_members",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )
            result["member_count"] = len(members)
            result["current_user_role"] = OrganizationRole(membership["role"])

            self.logger.log_info("Organization updated successfully", context)
            return OrganizationResponse(**result)
        except (NotFoundError, PermissionError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update organization", context)
            raise

    async def delete_organization(
        self,
        org_id: UUID,
        user_id: UUID,
        correlation_id: str,
    ) -> None:
        """
        Soft delete organization. Only owner can delete.

        Args:
            org_id: Organization ID
            user_id: Current user ID
            correlation_id: Request correlation ID

        Raises:
            NotFoundError: If organization not found
            PermissionError: If user is not owner
            ValidationError: If trying to delete personal org
        """
        context = {
            "operation": "delete_organization",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Deleting organization", context)

        try:
            # Check organization exists
            existing = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not existing or existing.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check if personal org
            if existing["type"] == "personal":
                raise ValidationError(
                    "Cannot delete personal organization", correlation_id
                )

            # Check permission - only owner can delete
            if existing["owner_id"] != str(user_id):
                raise PermissionError(
                    "Only the owner can delete this organization", correlation_id
                )

            # Soft delete
            self.supabase.update_record(
                "organizations",
                str(org_id),
                {"deleted_at": datetime.now(timezone.utc).isoformat()},
                correlation_id=correlation_id,
            )

            self.logger.log_info("Organization deleted successfully", context)
        except (NotFoundError, PermissionError, ValidationError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete organization", context)
            raise

    # =============================================
    # Member Management
    # =============================================

    async def add_member(
        self,
        org_id: UUID,
        data: OrganizationMemberAdd,
        user_id: UUID,
        correlation_id: str,
    ) -> OrganizationMemberResponse:
        """
        Add a user directly to an organization. Requires admin role.

        Args:
            org_id: Organization ID
            data: Member add data
            user_id: Current user ID (who is adding)
            correlation_id: Request correlation ID

        Returns:
            Added member
        """
        context = {
            "operation": "add_member",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
            "new_user_id": str(data.user_id),
        }
        self.logger.log_info("Adding member to organization", context)

        try:
            # Check organization exists
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check permission
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership or membership["role"] not in ("owner", "admin"):
                raise PermissionError(
                    "Admin or owner role required to add members", correlation_id
                )

            # Check if user is already a member
            existing = self._get_user_membership(
                org_id, data.user_id, correlation_id
            )
            if existing:
                raise ConflictError(
                    "User is already a member of this organization", correlation_id
                )

            # Add member
            member_data = {
                "org_id": str(org_id),
                "user_id": str(data.user_id),
                "role": data.role.value,
                "invited_by": str(user_id),
            }
            result = self.supabase.insert_record(
                "organization_members", member_data, correlation_id=correlation_id
            )

            # Fetch user details
            profile = self.supabase.get_record(
                "profiles", str(data.user_id), correlation_id=correlation_id
            )
            result["user_email"] = profile.get("email") if profile else None
            result["user_name"] = (
                f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                if profile
                else None
            )

            # Convert org type to team if was personal
            if org["type"] == "personal":
                self.supabase.update_record(
                    "organizations",
                    str(org_id),
                    {"type": OrganizationType.TEAM.value},
                    correlation_id=correlation_id,
                )
                self.logger.log_info(
                    "Organization converted from personal to team", context
                )

            self.logger.log_info("Member added successfully", context)
            return OrganizationMemberResponse(**result)
        except (NotFoundError, PermissionError, ConflictError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to add member", context)
            raise

    async def update_member_role(
        self,
        org_id: UUID,
        member_user_id: UUID,
        data: OrganizationMemberUpdate,
        user_id: UUID,
        correlation_id: str,
    ) -> OrganizationMemberResponse:
        """Update member role. Requires admin role."""
        context = {
            "operation": "update_member_role",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
            "member_user_id": str(member_user_id),
        }
        self.logger.log_info("Updating member role", context)

        try:
            # Check organization exists
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check permission
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership or membership["role"] not in ("owner", "admin"):
                raise PermissionError(
                    "Admin or owner role required to update members", correlation_id
                )

            # Check target membership exists
            target_membership = self._get_user_membership(
                org_id, member_user_id, correlation_id
            )
            if not target_membership:
                raise NotFoundError("Member not found", correlation_id)

            # Cannot change owner role
            if target_membership["role"] == "owner":
                raise ValidationError(
                    "Cannot change owner role", correlation_id
                )

            # Update role
            self.supabase.client.table("organization_members").update(
                {"role": data.role.value}
            ).eq("org_id", str(org_id)).eq(
                "user_id", str(member_user_id)
            ).execute()

            # Fetch updated membership
            updated = self._get_user_membership(
                org_id, member_user_id, correlation_id
            )

            # Fetch user details
            profile = self.supabase.get_record(
                "profiles", str(member_user_id), correlation_id=correlation_id
            )
            updated["user_email"] = profile.get("email") if profile else None
            updated["user_name"] = (
                f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                if profile
                else None
            )

            self.logger.log_info("Member role updated successfully", context)
            return OrganizationMemberResponse(**updated)
        except (NotFoundError, PermissionError, ValidationError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update member role", context)
            raise

    async def remove_member(
        self,
        org_id: UUID,
        member_user_id: UUID,
        user_id: UUID,
        correlation_id: str,
    ) -> None:
        """Remove member from organization."""
        context = {
            "operation": "remove_member",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
            "member_user_id": str(member_user_id),
        }
        self.logger.log_info("Removing member from organization", context)

        try:
            # Check organization exists
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check target membership exists
            target_membership = self._get_user_membership(
                org_id, member_user_id, correlation_id
            )
            if not target_membership:
                # Idempotent - already removed
                self.logger.log_info("Member already removed", context)
                return

            # Cannot remove owner
            if target_membership["role"] == "owner":
                raise ValidationError(
                    "Cannot remove organization owner", correlation_id
                )

            # Check permission: admins can remove members, members can remove self
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            is_self_removal = str(member_user_id) == str(user_id)

            if not is_self_removal:
                if not membership or membership["role"] not in ("owner", "admin"):
                    raise PermissionError(
                        "Admin or owner role required to remove members",
                        correlation_id,
                    )

            # Remove member
            self.supabase.client.table("organization_members").delete().eq(
                "org_id", str(org_id)
            ).eq("user_id", str(member_user_id)).execute()

            self.logger.log_info("Member removed successfully", context)
        except (NotFoundError, PermissionError, ValidationError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to remove member", context)
            raise

    async def list_members(
        self,
        org_id: UUID,
        user_id: UUID,
        correlation_id: str,
    ) -> list[OrganizationMemberResponse]:
        """List all members of an organization."""
        context = {
            "operation": "list_members",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Listing organization members", context)

        try:
            # Check organization exists and user is member
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership:
                raise PermissionError(
                    "You are not a member of this organization", correlation_id
                )

            # Get members
            members = self.supabase.query_records(
                "organization_members",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )

            # Fetch user details for each member
            member_responses = []
            for member in members:
                profile = self.supabase.get_record(
                    "profiles", member["user_id"], correlation_id=correlation_id
                )
                member["user_email"] = profile.get("email") if profile else None
                member["user_name"] = (
                    f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                    if profile
                    else None
                )
                member_responses.append(OrganizationMemberResponse(**member))

            return member_responses
        except (NotFoundError, PermissionError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list members", context)
            raise

    # =============================================
    # Invitation Management
    # =============================================

    async def create_invitation(
        self,
        org_id: UUID,
        data: OrganizationInvitationCreate,
        user_id: UUID,
        correlation_id: str,
    ) -> OrganizationInvitationResponse:
        """Create and send an invitation to join an organization."""
        context = {
            "operation": "create_invitation",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
            "email": data.email,
        }
        self.logger.log_info("Creating organization invitation", context)

        try:
            # Check organization exists
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check permission
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership or membership["role"] not in ("owner", "admin"):
                raise PermissionError(
                    "Admin or owner role required to send invitations",
                    correlation_id,
                )

            # Check if email is already a member (by checking profiles)
            profiles = self.supabase.query_records(
                "profiles",
                filters={"email": data.email},
                correlation_id=correlation_id,
            )
            if profiles:
                existing_member = self._get_user_membership(
                    org_id, UUID(profiles[0]["id"]), correlation_id
                )
                if existing_member:
                    raise ConflictError(
                        "User is already a member of this organization",
                        correlation_id,
                    )

            # Check for pending invitation
            pending = self.supabase.query_records(
                "organization_invitations",
                filters={"org_id": str(org_id), "email": data.email},
                correlation_id=correlation_id,
            )
            # Filter to only pending (not accepted)
            pending = [p for p in pending if not p.get("accepted_at")]
            if pending:
                raise ConflictError(
                    "A pending invitation already exists for this email",
                    correlation_id,
                )

            # Create invitation
            token = secrets.token_urlsafe(32)
            invitation_data = {
                "org_id": str(org_id),
                "email": data.email,
                "role": data.role.value,
                "token": token,
                "invited_by": str(user_id),
            }
            result = self.supabase.insert_record(
                "organization_invitations",
                invitation_data,
                correlation_id=correlation_id,
            )

            # Add display fields
            result["org_name"] = org["name"]
            profile = self.supabase.get_record(
                "profiles", str(user_id), correlation_id=correlation_id
            )
            result["inviter_name"] = (
                f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                if profile
                else None
            )

            # Convert org type to team if was personal
            if org["type"] == "personal":
                self.supabase.update_record(
                    "organizations",
                    str(org_id),
                    {"type": OrganizationType.TEAM.value},
                    correlation_id=correlation_id,
                )
                self.logger.log_info(
                    "Organization converted from personal to team", context
                )

            self.logger.log_info("Invitation created successfully", context)

            # Send organization invitation email (fail-safe: log error but don't block)
            try:
                inviter_name = result.get("inviter_name") or "A team member"

                await self.email_manager.send_template(
                    template_name="organization_invitation.html",
                    to_email=data.email,
                    subject=f"Join {org['name']} on {os.getenv('EMAIL_BRAND_NAME', 'SaaSForge')}",
                    context={
                        "org_name": org["name"],
                        "inviter_name": inviter_name,
                        "role": data.role.value,
                        "invite_url": f"{os.getenv('APP_URL', 'http://localhost:3000')}/accept-org-invite?token={token}",
                    },
                    correlation_id=correlation_id,
                )
                self.logger.log_info(
                    "Organization invitation email sent successfully", context
                )
            except Exception as e:
                # FAIL-SAFE: Log warning but don't fail the operation
                self.logger.log_warning(
                    "Failed to send organization invitation email - invitation created but email not sent",
                    {**context, "email_error": str(e), "error_type": type(e).__name__},
                )

            return OrganizationInvitationResponse(**result)
        except (NotFoundError, PermissionError, ConflictError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create invitation", context)
            raise

    async def list_invitations(
        self,
        org_id: UUID,
        user_id: UUID,
        correlation_id: str,
    ) -> list[OrganizationInvitationResponse]:
        """List pending invitations for an organization."""
        context = {
            "operation": "list_invitations",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Listing organization invitations", context)

        try:
            # Check organization exists
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check permission
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership or membership["role"] not in ("owner", "admin"):
                raise PermissionError(
                    "Admin or owner role required to view invitations",
                    correlation_id,
                )

            # Get invitations
            invitations = self.supabase.query_records(
                "organization_invitations",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )

            # Filter to pending only and add display fields
            invitation_responses = []
            for inv in invitations:
                if inv.get("accepted_at"):
                    continue  # Skip accepted invitations

                inv["org_name"] = org["name"]
                profile = self.supabase.get_record(
                    "profiles", inv["invited_by"], correlation_id=correlation_id
                )
                inv["inviter_name"] = (
                    f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                    if profile
                    else None
                )
                invitation_responses.append(OrganizationInvitationResponse(**inv))

            return invitation_responses
        except (NotFoundError, PermissionError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list invitations", context)
            raise

    async def cancel_invitation(
        self,
        org_id: UUID,
        invitation_id: UUID,
        user_id: UUID,
        correlation_id: str,
    ) -> None:
        """Cancel a pending invitation."""
        context = {
            "operation": "cancel_invitation",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
            "invitation_id": str(invitation_id),
        }
        self.logger.log_info("Canceling invitation", context)

        try:
            # Check organization exists
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Check permission
            membership = self._get_user_membership(org_id, user_id, correlation_id)
            if not membership or membership["role"] not in ("owner", "admin"):
                raise PermissionError(
                    "Admin or owner role required to cancel invitations",
                    correlation_id,
                )

            # Check invitation exists and belongs to this org
            invitation = self.supabase.get_record(
                "organization_invitations",
                str(invitation_id),
                correlation_id=correlation_id,
            )
            if not invitation or invitation["org_id"] != str(org_id):
                raise NotFoundError("Invitation not found", correlation_id)

            # Delete invitation
            self.supabase.delete_record(
                "organization_invitations",
                str(invitation_id),
                correlation_id=correlation_id,
            )

            self.logger.log_info("Invitation canceled successfully", context)
        except (NotFoundError, PermissionError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to cancel invitation", context)
            raise

    async def get_invitation_by_token(
        self,
        token: str,
        correlation_id: str,
    ) -> OrganizationInvitationPublicResponse:
        """Get invitation details by token (public endpoint)."""
        context = {
            "operation": "get_invitation_by_token",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "token_prefix": token[:8],
        }
        self.logger.log_info("Getting invitation by token", context)

        try:
            # Find invitation by token
            invitations = self.supabase.query_records(
                "organization_invitations",
                filters={"token": token},
                correlation_id=correlation_id,
            )
            if not invitations:
                raise NotFoundError("Invitation not found", correlation_id)

            invitation = invitations[0]

            # Check if already accepted
            if invitation.get("accepted_at"):
                raise ValidationError(
                    "This invitation has already been accepted", correlation_id
                )

            # Check expiration
            expires_at = datetime.fromisoformat(
                invitation["expires_at"].replace("Z", "+00:00")
            )
            is_expired = datetime.now(timezone.utc) > expires_at

            # Get organization
            org = self.supabase.get_record(
                "organizations",
                invitation["org_id"],
                correlation_id=correlation_id,
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization no longer exists", correlation_id)

            # Get inviter name
            profile = self.supabase.get_record(
                "profiles", invitation["invited_by"], correlation_id=correlation_id
            )
            inviter_name = (
                f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                if profile
                else None
            )

            return OrganizationInvitationPublicResponse(
                id=UUID(invitation["id"]),
                org_name=org["name"],
                org_avatar_url=org.get("avatar_url"),
                email=invitation["email"],
                role=OrganizationRole(invitation["role"]),
                inviter_name=inviter_name,
                expires_at=expires_at,
                is_expired=is_expired,
            )
        except (NotFoundError, ValidationError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get invitation by token", context)
            raise

    async def accept_invitation(
        self,
        token: str,
        user_id: UUID,
        correlation_id: str,
    ) -> OrganizationResponse:
        """Accept an invitation and join the organization."""
        context = {
            "operation": "accept_invitation",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "token_prefix": token[:8],
            "user_id": str(user_id),
        }
        self.logger.log_info("Accepting invitation", context)

        try:
            # Find invitation by token
            invitations = self.supabase.query_records(
                "organization_invitations",
                filters={"token": token},
                correlation_id=correlation_id,
            )
            if not invitations:
                raise NotFoundError("Invitation not found", correlation_id)

            invitation = invitations[0]

            # Check if already accepted
            if invitation.get("accepted_at"):
                raise ValidationError(
                    "This invitation has already been accepted", correlation_id
                )

            # Check expiration
            expires_at = datetime.fromisoformat(
                invitation["expires_at"].replace("Z", "+00:00")
            )
            if datetime.now(timezone.utc) > expires_at:
                raise ValidationError(
                    "This invitation has expired", correlation_id
                )

            # Verify email matches (get user's email from profile)
            profile = self.supabase.get_record(
                "profiles", str(user_id), correlation_id=correlation_id
            )
            if profile and profile.get("email", "").lower() != invitation["email"]:
                raise ValidationError(
                    "Your email does not match the invitation", correlation_id
                )

            # Check organization exists
            org = self.supabase.get_record(
                "organizations",
                invitation["org_id"],
                correlation_id=correlation_id,
            )
            if not org or org.get("deleted_at"):
                raise NotFoundError("Organization no longer exists", correlation_id)

            # Check if already a member
            existing = self._get_user_membership(
                UUID(invitation["org_id"]), user_id, correlation_id
            )
            if existing:
                raise ConflictError(
                    "You are already a member of this organization", correlation_id
                )

            # Add as member
            member_data = {
                "org_id": invitation["org_id"],
                "user_id": str(user_id),
                "role": invitation["role"],
                "invited_by": invitation["invited_by"],
            }
            self.supabase.insert_record(
                "organization_members", member_data, correlation_id=correlation_id
            )

            # Mark invitation as accepted
            self.supabase.update_record(
                "organization_invitations",
                invitation["id"],
                {"accepted_at": datetime.now(timezone.utc).isoformat()},
                correlation_id=correlation_id,
            )

            self.logger.log_info(
                "Invitation accepted successfully",
                {**context, "org_id": invitation["org_id"]},
            )

            # Return organization
            return await self.get_organization(
                UUID(invitation["org_id"]), user_id, correlation_id
            )
        except (NotFoundError, ValidationError, ConflictError):
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to accept invitation", context)
            raise

    # =============================================
    # Helper Methods
    # =============================================

    def _generate_slug(self, name: str, owner_id: UUID) -> str:
        """Generate a unique slug from organization name."""
        # For personal orgs
        if name == "Personal":
            return f"personal-{str(owner_id)[:8]}"

        # Convert name to slug
        slug = name.lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = re.sub(r"^-+|-+$", "", slug)

        # Ensure slug is not empty
        if not slug:
            slug = f"org-{str(owner_id)[:8]}"

        return slug

    def _get_user_membership(
        self, org_id: UUID, user_id: UUID, correlation_id: str
    ) -> dict | None:
        """Get user's membership in an organization."""
        memberships = self.supabase.query_records(
            "organization_members",
            filters={"org_id": str(org_id), "user_id": str(user_id)},
            correlation_id=correlation_id,
        )
        return memberships[0] if memberships else None

    async def get_user_organization_count(
        self, user_id: UUID, correlation_id: str
    ) -> int:
        """Get count of organizations user belongs to."""
        memberships = self.supabase.query_records(
            "organization_members",
            filters={"user_id": str(user_id)},
            correlation_id=correlation_id,
        )
        return len(memberships)

    async def get_user_default_organization(
        self, user_id: UUID, correlation_id: str
    ) -> OrganizationResponse | None:
        """Get user's default organization (personal or first)."""
        context = {
            "operation": "get_user_default_organization",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }

        try:
            # Use RPC function
            result = self.supabase.client.rpc(
                "get_user_default_organization", {"p_user_id": str(user_id)}
            ).execute()

            if result.data:
                org_id = result.data
                if org_id:
                    return await self.get_organization(
                        UUID(org_id), user_id, correlation_id
                    )

            return None
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get default organization", context)
            return None

    # =============================================
    # Admin Methods (System-wide access)
    # =============================================

    async def list_all_organizations_admin(
        self,
        correlation_id: str,
        page: int = 1,
        page_size: int = 50,
        org_type: str | None = None,
        include_deleted: bool = False,
    ) -> OrganizationListResponse:
        """
        List all organizations (admin only).

        Args:
            correlation_id: Request correlation ID
            page: Page number
            page_size: Page size
            org_type: Filter by type (personal/team)
            include_deleted: Include soft-deleted orgs

        Returns:
            Paginated list of all organizations
        """
        context = {
            "operation": "list_all_organizations_admin",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "page": page,
            "page_size": page_size,
        }
        self.logger.log_info("Listing all organizations (admin)", context)

        try:
            # Build filters
            filters = {}
            if org_type:
                filters["type"] = org_type

            # Get all organizations
            all_orgs = self.supabase.query_records(
                "organizations",
                filters=filters,
                correlation_id=correlation_id,
            )

            # Filter deleted orgs unless requested
            if not include_deleted:
                all_orgs = [org for org in all_orgs if not org.get("deleted_at")]

            # Sort by created_at descending
            all_orgs.sort(key=lambda x: x.get("created_at", ""), reverse=True)

            # Calculate pagination
            total = len(all_orgs)
            total_pages = math.ceil(total / page_size) if total > 0 else 1
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_orgs = all_orgs[start_idx:end_idx]

            # Add member counts
            org_responses = []
            for org in page_orgs:
                members = self.supabase.query_records(
                    "organization_members",
                    filters={"org_id": org["id"]},
                    correlation_id=correlation_id,
                )
                org["member_count"] = len(members)
                org["current_user_role"] = None  # Admin view, no current user role
                org_responses.append(OrganizationResponse(**org))

            return OrganizationListResponse(
                items=org_responses,
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            )
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list all organizations", context)
            raise

    async def get_organization_detail_admin(
        self,
        org_id: UUID,
        correlation_id: str,
    ) -> OrganizationDetailResponse:
        """
        Get organization details (admin only).

        Args:
            org_id: Organization ID
            correlation_id: Request correlation ID

        Returns:
            Organization details with members and invitations
        """
        context = {
            "operation": "get_organization_detail_admin",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Getting organization detail (admin)", context)

        try:
            # Get organization
            result = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not result:
                raise NotFoundError("Organization not found", correlation_id)

            # Get member count
            members = self.supabase.query_records(
                "organization_members",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )
            result["member_count"] = len(members)
            result["current_user_role"] = None  # Admin view

            # Get member details
            member_responses = []
            for member in members:
                profile = self.supabase.get_record(
                    "profiles", member["user_id"], correlation_id=correlation_id
                )
                member["user_email"] = profile.get("email") if profile else None
                member["user_name"] = (
                    f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                    if profile
                    else None
                )
                member_responses.append(OrganizationMemberResponse(**member))

            # Get pending invitations
            invitations = self.supabase.query_records(
                "organization_invitations",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )
            invitation_responses = []
            for inv in invitations:
                if inv.get("accepted_at"):
                    continue  # Skip accepted
                inv["org_name"] = result["name"]
                profile = self.supabase.get_record(
                    "profiles", inv["invited_by"], correlation_id=correlation_id
                )
                inv["inviter_name"] = (
                    f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                    if profile
                    else None
                )
                invitation_responses.append(OrganizationInvitationResponse(**inv))

            # Build response
            org_dict = result
            org_dict["members"] = [m.model_dump() for m in member_responses]
            org_dict["pending_invitations"] = [
                i.model_dump() for i in invitation_responses
            ]

            return OrganizationDetailResponse(**org_dict)
        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get organization detail", context)
            raise

    async def update_organization_admin(
        self,
        org_id: UUID,
        data: OrganizationUpdate,
        correlation_id: str,
    ) -> OrganizationResponse:
        """
        Update organization (admin only).

        Args:
            org_id: Organization ID
            data: Update data
            correlation_id: Request correlation ID

        Returns:
            Updated organization
        """
        context = {
            "operation": "update_organization_admin",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Updating organization (admin)", context)

        try:
            # Check organization exists
            existing = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Organization not found", correlation_id)

            # Update organization
            update_data = data.model_dump(exclude_unset=True)
            result = self.supabase.update_record(
                "organizations",
                str(org_id),
                update_data,
                correlation_id=correlation_id,
            )

            # Add member count
            members = self.supabase.query_records(
                "organization_members",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )
            result["member_count"] = len(members)
            result["current_user_role"] = None  # Admin view

            self.logger.log_info("Organization updated successfully (admin)", context)
            return OrganizationResponse(**result)
        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update organization", context)
            raise

    async def delete_organization_admin(
        self,
        org_id: UUID,
        correlation_id: str,
    ) -> None:
        """
        Delete organization (admin only).

        Admins can delete any organization, including personal orgs.

        Args:
            org_id: Organization ID
            correlation_id: Request correlation ID
        """
        context = {
            "operation": "delete_organization_admin",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Deleting organization (admin)", context)

        try:
            # Check organization exists
            existing = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not existing or existing.get("deleted_at"):
                raise NotFoundError("Organization not found", correlation_id)

            # Soft delete (admin can delete personal orgs too)
            self.supabase.update_record(
                "organizations",
                str(org_id),
                {"deleted_at": datetime.now(timezone.utc).isoformat()},
                correlation_id=correlation_id,
            )

            self.logger.log_info("Organization deleted successfully (admin)", context)
        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete organization", context)
            raise

    async def list_members_admin(
        self,
        org_id: UUID,
        correlation_id: str,
    ) -> list[OrganizationMemberResponse]:
        """
        List members of any organization (admin only).

        Args:
            org_id: Organization ID
            correlation_id: Request correlation ID

        Returns:
            List of members
        """
        context = {
            "operation": "list_members_admin",
            "component": "OrganizationService",
            "correlation_id": correlation_id,
            "org_id": str(org_id),
        }
        self.logger.log_info("Listing organization members (admin)", context)

        try:
            # Check organization exists
            org = self.supabase.get_record(
                "organizations", str(org_id), correlation_id=correlation_id
            )
            if not org:
                raise NotFoundError("Organization not found", correlation_id)

            # Get members
            members = self.supabase.query_records(
                "organization_members",
                filters={"org_id": str(org_id)},
                correlation_id=correlation_id,
            )

            # Fetch user details for each member
            member_responses = []
            for member in members:
                profile = self.supabase.get_record(
                    "profiles", member["user_id"], correlation_id=correlation_id
                )
                member["user_email"] = profile.get("email") if profile else None
                member["user_name"] = (
                    f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
                    if profile
                    else None
                )
                member_responses.append(OrganizationMemberResponse(**member))

            return member_responses
        except NotFoundError:
            raise
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list members", context)
            raise
