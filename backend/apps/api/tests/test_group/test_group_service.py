"""Unit tests for UserGroupService."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.api.api.exceptions.domain_exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from apps.api.api.models.group.group_models import (
    GroupFeatureAssign,
    GroupMemberAdd,
    UserGroupCreate,
    UserGroupUpdate,
)
from apps.api.api.services.group.group_service import UserGroupService


@pytest.fixture
def mock_supabase():
    """Mock SupabaseManager for testing."""
    # SupabaseManager / local_store data methods are SYNCHRONOUS (def, not
    # async def) — the service awaits its own coroutines but calls these
    # plainly. Mock them with MagicMock, not AsyncMock, or they return
    # un-awaited coroutines ("object of type 'coroutine' has no len()").
    supabase = MagicMock()
    supabase.query_records = MagicMock()
    supabase.get_record = MagicMock()
    supabase.insert_record = MagicMock()
    supabase.update_record = MagicMock()
    supabase.delete_record = MagicMock()
    supabase.client = MagicMock()
    return supabase


@pytest.fixture
def mock_logger():
    """Mock LoggingController for testing."""
    logger = MagicMock()
    return logger


@pytest.fixture
def group_service(mock_supabase, mock_logger):
    """Create UserGroupService with mocked dependencies."""
    return UserGroupService(supabase=mock_supabase, logger=mock_logger)


@pytest.fixture
def sample_group_id():
    """Sample group UUID."""
    return uuid.uuid4()


@pytest.fixture
def sample_user_id():
    """Sample user UUID."""
    return uuid.uuid4()


@pytest.fixture
def sample_admin_id():
    """Sample admin UUID."""
    return uuid.uuid4()


@pytest.fixture
def sample_feature_id():
    """Sample feature UUID."""
    return uuid.uuid4()


class TestCreateGroup:
    """Tests for creating user groups."""

    @pytest.mark.asyncio
    async def test_create_group_success(
        self, group_service, sample_admin_id, mock_supabase
    ):
        """Test successful group creation."""
        # Arrange
        group_data = UserGroupCreate(
            name="Test Group", description="Test description"
        )
        mock_supabase.query_records.return_value = []  # No existing group
        mock_supabase.insert_record.return_value = {
            "id": str(uuid.uuid4()),
            "name": "Test Group",
            "description": "Test description",
            "is_active": True,
            "created_by": str(sample_admin_id),
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        # Act
        result = await group_service.create_group(
            group_data, sample_admin_id, "test-correlation"
        )

        # Assert
        assert result.name == "Test Group"
        assert result.description == "Test description"
        assert result.member_count == 0
        mock_supabase.query_records.assert_called_once()
        mock_supabase.insert_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_group_duplicate_name(
        self, group_service, sample_admin_id, mock_supabase
    ):
        """Test creating group with duplicate name fails."""
        # Arrange
        group_data = UserGroupCreate(name="Existing Group")
        mock_supabase.query_records.return_value = [
            {"id": str(uuid.uuid4()), "name": "Existing Group"}
        ]

        # Act & Assert
        with pytest.raises(ConflictError):
            await group_service.create_group(
                group_data, sample_admin_id, "test-correlation"
            )


class TestGetGroup:
    """Tests for retrieving user groups."""

    @pytest.mark.asyncio
    async def test_get_group_success(
        self, group_service, sample_group_id, mock_supabase
    ):
        """Test successful group retrieval."""
        # Arrange
        mock_supabase.get_record.return_value = {
            "id": str(sample_group_id),
            "name": "Test Group",
            "description": "Test description",
            "is_active": True,
            "created_by": str(uuid.uuid4()),
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_supabase.query_records.return_value = []  # No members

        # Act
        result = await group_service.get_group(
            sample_group_id, "test-correlation"
        )

        # Assert
        assert str(result.id) == str(sample_group_id)
        assert result.name == "Test Group"
        assert result.member_count == 0

    @pytest.mark.asyncio
    async def test_get_group_not_found(
        self, group_service, sample_group_id, mock_supabase
    ):
        """Test getting non-existent group fails."""
        # Arrange
        mock_supabase.get_record.return_value = None

        # Act & Assert
        with pytest.raises(NotFoundError):
            await group_service.get_group(sample_group_id, "test-correlation")


class TestUpdateGroup:
    """Tests for updating user groups."""

    @pytest.mark.asyncio
    async def test_update_group_success(
        self, group_service, sample_group_id, mock_supabase
    ):
        """Test successful group update."""
        # Arrange
        update_data = UserGroupUpdate(name="Updated Group")
        mock_supabase.get_record.return_value = {
            "id": str(sample_group_id),
            "name": "Old Name",
            "is_active": True,
        }
        mock_supabase.query_records.return_value = []  # Name is unique
        mock_supabase.update_record.return_value = {
            "id": str(sample_group_id),
            "name": "Updated Group",
            "description": None,
            "is_active": True,
            "created_by": str(uuid.uuid4()),
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T12:00:00Z",
        }

        # Act
        result = await group_service.update_group(
            sample_group_id, update_data, "test-correlation"
        )

        # Assert
        assert result.name == "Updated Group"
        mock_supabase.update_record.assert_called_once()


class TestAddMember:
    """Tests for adding members to groups."""

    @pytest.mark.asyncio
    async def test_add_member_success(
        self,
        group_service,
        sample_group_id,
        sample_user_id,
        sample_admin_id,
        mock_supabase,
    ):
        """Test successfully adding member to group."""
        # Arrange
        member_data = GroupMemberAdd(user_id=sample_user_id)
        mock_supabase.get_record.side_effect = [
            {"id": str(sample_group_id), "is_active": True},  # Group exists
            {"id": str(sample_user_id), "email": "test@example.com"},  # User exists
        ]
        mock_supabase.query_records.side_effect = [
            [],  # No existing membership
            [{"name": "Test User"}],  # User profile
        ]
        mock_supabase.insert_record.return_value = {
            "user_id": str(sample_user_id),
            "group_id": str(sample_group_id),
            "added_by": str(sample_admin_id),
            "added_at": "2024-01-01T00:00:00Z",
        }

        # Act
        result = await group_service.add_member(
            sample_group_id, member_data, sample_admin_id, "test-correlation"
        )

        # Assert
        assert str(result.user_id) == str(sample_user_id)
        assert str(result.group_id) == str(sample_group_id)
        mock_supabase.insert_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_member_to_inactive_group(
        self,
        group_service,
        sample_group_id,
        sample_user_id,
        sample_admin_id,
        mock_supabase,
    ):
        """Test adding member to inactive group fails."""
        # Arrange
        member_data = GroupMemberAdd(user_id=sample_user_id)
        mock_supabase.get_record.return_value = {
            "id": str(sample_group_id),
            "is_active": False,
        }

        # Act & Assert
        with pytest.raises(ValidationError):
            await group_service.add_member(
                sample_group_id, member_data, sample_admin_id, "test-correlation"
            )


class TestAssignFeature:
    """Tests for assigning features to groups."""

    @pytest.mark.asyncio
    async def test_assign_feature_success(
        self,
        group_service,
        sample_group_id,
        sample_feature_id,
        sample_admin_id,
        mock_supabase,
    ):
        """Test successfully assigning feature to group."""
        # Arrange
        feature_data = GroupFeatureAssign(
            feature_id=sample_feature_id, value=True
        )
        mock_supabase.get_record.side_effect = [
            {"id": str(sample_group_id), "is_active": True},  # Group exists
            {
                "id": str(sample_feature_id),
                "key": "test_feature",
                "name": "Test Feature",
            },  # Feature exists
        ]
        mock_supabase.query_records.return_value = []  # Not already assigned
        mock_supabase.insert_record.return_value = {
            "group_id": str(sample_group_id),
            "feature_id": str(sample_feature_id),
            "value": True,
            "added_by": str(sample_admin_id),
            "created_at": "2024-01-01T00:00:00Z",
        }

        # Act
        result = await group_service.assign_feature(
            sample_group_id, feature_data, sample_admin_id, "test-correlation"
        )

        # Assert
        assert str(result.group_id) == str(sample_group_id)
        assert str(result.feature_id) == str(sample_feature_id)
        assert result.value is True
        mock_supabase.insert_record.assert_called_once()


class TestCheckUserFeatureViaGroups:
    """Tests for checking user feature access via groups."""

    @pytest.mark.asyncio
    async def test_check_user_has_feature_via_group(
        self, group_service, sample_user_id, mock_supabase
    ):
        """Test user has access to feature via group."""
        # Arrange — source is now provider-agnostic: it scans
        # get_user_features_via_groups() for the key, not a Supabase RPC.
        group_service.get_user_features_via_groups = AsyncMock(
            return_value=[
                MagicMock(feature_key="test_feature", value=True),
            ]
        )

        # Act
        result = await group_service.check_user_feature_via_groups(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert result is True
        group_service.get_user_features_via_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_user_no_feature_via_group(
        self, group_service, sample_user_id, mock_supabase
    ):
        """Test user has no access to feature via group."""
        # Arrange — no matching feature key among the user's group features.
        group_service.get_user_features_via_groups = AsyncMock(
            return_value=[
                MagicMock(feature_key="other_feature", value=True),
            ]
        )

        # Act
        result = await group_service.check_user_feature_via_groups(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert result is None
