"""User group service for RBAC system."""

from __future__ import annotations

import math
from uuid import UUID

from libs.log_manager.controller import LoggingController
from libs.supabase.supabase import SupabaseManager
from libs.datastore import get_data_store
from apps.api.api.exceptions.domain_exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from apps.api.api.models.group.group_models import (
    GroupFeatureAssign,
    GroupFeatureResponse,
    GroupMemberAdd,
    GroupMemberResponse,
    UserGroupCreate,
    UserGroupDetailResponse,
    UserGroupListResponse,
    UserGroupResponse,
    UserGroupUpdate,
)


class UserGroupService:
    """Service for handling user group operations."""

    def __init__(
        self,
        access_token: str | None = None,
        supabase: SupabaseManager | None = None,
        logger: LoggingController | None = None,
    ):
        self.supabase = supabase or get_data_store(access_token=access_token)
        self.logger = logger or LoggingController(app_name="UserGroupService")

    # =============================================
    # Group CRUD Operations
    # =============================================

    async def create_group(
        self, data: UserGroupCreate, admin_id: UUID, correlation_id: str
    ) -> UserGroupResponse:
        """
        Create a new user group.

        Args:
            data: Group creation data
            admin_id: ID of the admin creating the group
            correlation_id: Request correlation ID

        Returns:
            Created group

        Raises:
            ConflictError: If group name already exists
            ValidationError: If validation fails
        """
        context = {
            "operation": "create_group",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_name": data.name,
        }
        self.logger.log_info("Creating user group", context)

        try:
            # Check if name is unique
            existing = self.supabase.query_records(
                "user_groups",
                filters={"name": data.name},
                correlation_id=correlation_id,
            )
            if existing:
                raise ConflictError(
                    f"Group '{data.name}' already exists", correlation_id
                )

            # Create group
            group_data = data.model_dump()
            group_data["created_by"] = str(admin_id)
            result = self.supabase.insert_record(
                "user_groups", group_data, correlation_id=correlation_id
            )

            # Add member count
            result["member_count"] = 0

            self.logger.log_info(
                "Group created successfully",
                {**context, "group_id": result["id"]},
            )
            return UserGroupResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to create group", context)
            raise

    async def get_group(
        self, group_id: UUID, correlation_id: str
    ) -> UserGroupResponse:
        """Get group by ID with member count."""
        context = {
            "operation": "get_group",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
        }
        self.logger.log_info("Getting group", context)

        try:
            result = self.supabase.get_record(
                "user_groups", str(group_id), correlation_id=correlation_id
            )
            if not result:
                raise NotFoundError("Group not found", correlation_id)

            # Get member count
            members = self.supabase.query_records(
                "user_group_members",
                filters={"group_id": str(group_id)},
                correlation_id=correlation_id,
            )
            result["member_count"] = len(members)

            return UserGroupResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get group", context)
            raise

    async def get_group_detail(
        self, group_id: UUID, correlation_id: str
    ) -> UserGroupDetailResponse:
        """Get group with full details (members and features)."""
        context = {
            "operation": "get_group_detail",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
        }
        self.logger.log_info("Getting group detail", context)

        try:
            # Get base group info
            group = await self.get_group(group_id, correlation_id)

            # Get members
            members = await self.list_members(group_id, correlation_id)

            # Get features
            features = await self.list_features(group_id, correlation_id)

            # Build response
            group_dict = group.model_dump()
            group_dict["members"] = [m.model_dump() for m in members]
            group_dict["features"] = [f.model_dump() for f in features]

            return UserGroupDetailResponse(**group_dict)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get group detail", context)
            raise

    async def list_groups(
        self,
        correlation_id: str,
        page: int = 1,
        page_size: int = 50,
        is_active: bool | None = None,
    ) -> UserGroupListResponse:
        """
        List all groups with pagination using view with pre-computed counts.

        This method uses the user_groups_with_counts view which includes
        member_count via subquery, eliminating N queries for member counts.
        """
        context = {
            "operation": "list_groups",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "page": page,
            "page_size": page_size,
        }
        self.logger.log_info("Listing groups", context)

        try:
            # Build filters
            filters = {}
            if is_active is not None:
                filters["is_active"] = is_active

            # Single query with subquery counts (replaces N+1 queries)
            all_groups = self.supabase.query_records(
                "user_groups_with_counts",
                filters=filters,
                correlation_id=correlation_id,
            )

            # Sort by created_at descending
            all_groups.sort(
                key=lambda x: x.get("created_at", ""), reverse=True
            )

            # Calculate pagination
            total = len(all_groups)
            total_pages = math.ceil(total / page_size) if total > 0 else 1
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            page_groups = all_groups[start_idx:end_idx]

            return UserGroupListResponse(
                items=[UserGroupResponse(**g) for g in page_groups],
                total=total,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
            )
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list groups", context)
            raise

    async def update_group(
        self, group_id: UUID, data: UserGroupUpdate, correlation_id: str
    ) -> UserGroupResponse:
        """Update group."""
        context = {
            "operation": "update_group",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
        }
        self.logger.log_info("Updating group", context)

        try:
            # Check if group exists
            existing = self.supabase.get_record(
                "user_groups", str(group_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Group not found", correlation_id)

            # Check name uniqueness if updating name
            if data.name and data.name != existing.get("name"):
                name_check = self.supabase.query_records(
                    "user_groups",
                    filters={"name": data.name},
                    correlation_id=correlation_id,
                )
                if name_check:
                    raise ConflictError(
                        f"Group '{data.name}' already exists", correlation_id
                    )

            # Update group
            update_data = data.model_dump(exclude_unset=True)
            result = self.supabase.update_record(
                "user_groups",
                str(group_id),
                update_data,
                correlation_id=correlation_id,
            )

            # Add member count
            members = self.supabase.query_records(
                "user_group_members",
                filters={"group_id": str(group_id)},
                correlation_id=correlation_id,
            )
            result["member_count"] = len(members)

            self.logger.log_info("Group updated successfully", context)
            return UserGroupResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to update group", context)
            raise

    async def delete_group(
        self, group_id: UUID, correlation_id: str
    ) -> None:
        """Delete group (soft delete via is_active=false)."""
        context = {
            "operation": "delete_group",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
        }
        self.logger.log_info("Deleting group", context)

        try:
            # Check if group exists
            existing = self.supabase.get_record(
                "user_groups", str(group_id), correlation_id=correlation_id
            )
            if not existing:
                raise NotFoundError("Group not found", correlation_id)

            # Soft delete
            self.supabase.update_record(
                "user_groups",
                str(group_id),
                {"is_active": False},
                correlation_id=correlation_id,
            )

            self.logger.log_info("Group deleted successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to delete group", context)
            raise

    # =============================================
    # Member Management
    # =============================================

    async def add_member(
        self,
        group_id: UUID,
        data: GroupMemberAdd,
        admin_id: UUID,
        correlation_id: str,
    ) -> GroupMemberResponse:
        """Add a user to a group."""
        context = {
            "operation": "add_member",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
            "user_id": str(data.user_id),
        }
        self.logger.log_info("Adding member to group", context)

        try:
            # Verify group exists and is active
            group = self.supabase.get_record(
                "user_groups", str(group_id), correlation_id=correlation_id
            )
            if not group:
                raise NotFoundError("Group not found", correlation_id)
            if not group.get("is_active", True):
                raise ValidationError("Cannot add members to inactive group", correlation_id)

            # Verify user exists
            user = self.supabase.get_record(
                "auth.users", str(data.user_id), correlation_id=correlation_id
            )
            if not user:
                raise ValidationError("User not found", correlation_id)

            # Check if already a member
            existing = self.supabase.query_records(
                "user_group_members",
                filters={"group_id": str(group_id), "user_id": str(data.user_id)},
                correlation_id=correlation_id,
            )
            if existing:
                raise ConflictError(
                    "User is already a member of this group", correlation_id
                )

            # Add member
            member_data = {
                "group_id": str(group_id),
                "user_id": str(data.user_id),
                "added_by": str(admin_id),
            }
            result = self.supabase.insert_record(
                "user_group_members", member_data, correlation_id=correlation_id
            )

            # Fetch user details for response
            profile = self.supabase.query_records(
                "profiles",
                filters={"id": str(data.user_id)},
                correlation_id=correlation_id,
            )
            result["user_email"] = user.get("email")
            result["user_name"] = profile[0].get("name") if profile else None

            self.logger.log_info("Member added successfully", context)
            return GroupMemberResponse(**result)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to add member", context)
            raise

    async def remove_member(
        self, group_id: UUID, user_id: UUID, correlation_id: str
    ) -> None:
        """Remove a user from a group."""
        context = {
            "operation": "remove_member",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
            "user_id": str(user_id),
        }
        self.logger.log_info("Removing member from group", context)

        try:
            # Check if membership exists
            existing = self.supabase.query_records(
                "user_group_members",
                filters={"group_id": str(group_id), "user_id": str(user_id)},
                correlation_id=correlation_id,
            )
            if not existing:
                # Idempotent - already removed
                self.logger.log_info("Member already removed", context)
                return

            # Remove member (delete from junction table)
            # We need to use raw SQL for composite key delete
            self.supabase.client.table("user_group_members").delete().eq(
                "group_id", str(group_id)
            ).eq("user_id", str(user_id)).execute()

            self.logger.log_info("Member removed successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to remove member", context)
            raise

    async def list_members(
        self, group_id: UUID, correlation_id: str
    ) -> list[GroupMemberResponse]:
        """List all members of a group."""
        context = {
            "operation": "list_members",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
        }
        self.logger.log_info("Listing group members", context)

        try:
            members = self.supabase.query_records(
                "user_group_members",
                filters={"group_id": str(group_id)},
                correlation_id=correlation_id,
            )

            # Fetch user details for each member
            member_responses = []
            for member in members:
                user = self.supabase.get_record(
                    "auth.users", member["user_id"], correlation_id=correlation_id
                )
                profile = self.supabase.query_records(
                    "profiles",
                    filters={"id": member["user_id"]},
                    correlation_id=correlation_id,
                )
                member["user_email"] = user.get("email") if user else None
                member["user_name"] = profile[0].get("name") if profile else None
                member_responses.append(GroupMemberResponse(**member))

            return member_responses
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list members", context)
            raise

    # =============================================
    # Feature Management
    # =============================================

    async def assign_feature(
        self,
        group_id: UUID,
        data: GroupFeatureAssign,
        admin_id: UUID,
        correlation_id: str,
    ) -> GroupFeatureResponse:
        """Assign a feature to a group."""
        context = {
            "operation": "assign_feature",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
            "feature_id": str(data.feature_id),
        }
        self.logger.log_info("Assigning feature to group", context)

        try:
            # Verify group exists and is active
            group = self.supabase.get_record(
                "user_groups", str(group_id), correlation_id=correlation_id
            )
            if not group:
                raise NotFoundError("Group not found", correlation_id)
            if not group.get("is_active", True):
                raise ValidationError(
                    "Cannot assign features to inactive group", correlation_id
                )

            # Verify feature exists
            feature = self.supabase.get_record(
                "features", str(data.feature_id), correlation_id=correlation_id
            )
            if not feature:
                raise ValidationError("Feature not found", correlation_id)

            # Check if feature already assigned
            existing = self.supabase.query_records(
                "user_group_features",
                filters={
                    "group_id": str(group_id),
                    "feature_id": str(data.feature_id),
                },
                correlation_id=correlation_id,
            )
            if existing:
                # Update existing assignment
                feature_data = {
                    "value": data.value,
                }
                # Use raw SQL for composite key update
                result = (
                    self.supabase.client.table("user_group_features")
                    .update(feature_data)
                    .eq("group_id", str(group_id))
                    .eq("feature_id", str(data.feature_id))
                    .execute()
                )
                result_data = result.data[0] if result.data else existing[0]
            else:
                # Create new assignment
                feature_data = {
                    "group_id": str(group_id),
                    "feature_id": str(data.feature_id),
                    "value": data.value,
                    "added_by": str(admin_id),
                }
                result = self.supabase.insert_record(
                    "user_group_features",
                    feature_data,
                    correlation_id=correlation_id,
                )
                result_data = result

            # Add feature details for response
            result_data["feature_key"] = feature["key"]
            result_data["feature_name"] = feature["name"]

            self.logger.log_info("Feature assigned successfully", context)
            return GroupFeatureResponse(**result_data)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to assign feature", context)
            raise

    async def revoke_feature(
        self, group_id: UUID, feature_id: UUID, correlation_id: str
    ) -> None:
        """Revoke a feature from a group."""
        context = {
            "operation": "revoke_feature",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
            "feature_id": str(feature_id),
        }
        self.logger.log_info("Revoking feature from group", context)

        try:
            # Check if assignment exists
            existing = self.supabase.query_records(
                "user_group_features",
                filters={"group_id": str(group_id), "feature_id": str(feature_id)},
                correlation_id=correlation_id,
            )
            if not existing:
                # Idempotent - already revoked
                self.logger.log_info("Feature already revoked", context)
                return

            # Revoke feature (delete from junction table)
            self.supabase.client.table("user_group_features").delete().eq(
                "group_id", str(group_id)
            ).eq("feature_id", str(feature_id)).execute()

            self.logger.log_info("Feature revoked successfully", context)
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to revoke feature", context)
            raise

    async def list_features(
        self, group_id: UUID, correlation_id: str
    ) -> list[GroupFeatureResponse]:
        """List all features assigned to a group."""
        context = {
            "operation": "list_features",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "group_id": str(group_id),
        }
        self.logger.log_info("Listing group features", context)

        try:
            features = self.supabase.query_records(
                "user_group_features",
                filters={"group_id": str(group_id)},
                correlation_id=correlation_id,
            )

            # Fetch feature details for each
            feature_responses = []
            for feature_assignment in features:
                feature = self.supabase.get_record(
                    "features",
                    feature_assignment["feature_id"],
                    correlation_id=correlation_id,
                )
                feature_assignment["feature_key"] = (
                    feature["key"] if feature else ""
                )
                feature_assignment["feature_name"] = (
                    feature["name"] if feature else ""
                )
                feature_responses.append(
                    GroupFeatureResponse(**feature_assignment)
                )

            return feature_responses
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to list features", context)
            raise

    # =============================================
    # User-Specific Operations
    # =============================================

    async def get_user_groups(
        self, user_id: UUID, correlation_id: str
    ) -> list[UserGroupResponse]:
        """
        Get all groups a user belongs to using optimized RPC function.

        This method uses the get_user_groups_with_details RPC function which
        performs a single query with JOINs and aggregations, replacing 1+2N queries.
        """
        context = {
            "operation": "get_user_groups",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Getting user groups", context)

        try:
            # Single RPC call (replaces 1+2N queries)
            result = self.supabase.execute_rpc(
                "get_user_groups_with_details",
                params={"p_user_id": str(user_id)},
                correlation_id=correlation_id,
                cache_ttl=300,  # 5 minutes
            )

            return [UserGroupResponse(**group) for group in result]
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get user groups", context)
            raise

    async def get_user_features_via_groups(
        self, user_id: UUID, correlation_id: str
    ) -> list[GroupFeatureResponse]:
        """Get all features accessible to a user via their groups."""
        context = {
            "operation": "get_user_features_via_groups",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
        }
        self.logger.log_info("Getting user features via groups", context)

        try:
            # Use the SQL function to get features
            result = (
                self.supabase.client.rpc(
                    "get_user_features_via_groups", {"p_user_id": str(user_id)}
                )
                .execute()
            )

            feature_responses = []
            if result.data:
                for feature_data in result.data:
                    feature_responses.append(
                        GroupFeatureResponse(
                            group_id=feature_data["group_id"],
                            feature_id=feature_data["feature_id"],
                            feature_key=feature_data["feature_key"],
                            feature_name=feature_data["feature_name"],
                            value=feature_data["value"],
                            created_at=feature_data.get("created_at"),
                        )
                    )

            return feature_responses
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error("Failed to get user features via groups", context)
            raise

    async def check_user_feature_via_groups(
        self, user_id: UUID, feature_key: str, correlation_id: str
    ) -> dict | bool | int | str | None:
        """
        Check if user has access to a feature via their groups.

        Returns:
            Feature value if accessible, None otherwise
        """
        context = {
            "operation": "check_user_feature_via_groups",
            "component": "UserGroupService",
            "correlation_id": correlation_id,
            "user_id": str(user_id),
            "feature_key": feature_key,
        }
        self.logger.log_info("Checking user feature via groups", context)

        try:
            # Use the SQL function to get feature value
            result = (
                self.supabase.client.rpc(
                    "get_user_feature_via_groups",
                    {"p_user_id": str(user_id), "p_feature_key": feature_key},
                )
                .execute()
            )

            if result.data and len(result.data) > 0:
                return result.data[0]["value"]

            return None
        except Exception as e:
            context["error"] = str(e)
            self.logger.log_error(
                "Failed to check user feature via groups", context
            )
            raise
