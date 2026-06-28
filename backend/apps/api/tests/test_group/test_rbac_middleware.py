"""Integration tests for RBAC middleware."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from apps.api.api.middleware.rbac_middleware import RBACMiddleware


@pytest.fixture
def mock_supabase():
    """Mock SupabaseManager for testing."""
    supabase = MagicMock()
    supabase.query_records = MagicMock()
    supabase.client = MagicMock()
    # _check_feature_access short-circuits to the in-memory cache when
    # enable_cache is truthy; an unconfigured MagicMock cache returns a
    # MagicMock for cache.get(...)["has_access"]. Disable so tests exercise
    # the query path they stub via query_records.side_effect.
    supabase.enable_cache = False
    return supabase


@pytest.fixture
def mock_logger():
    """Mock LoggingController for testing."""
    logger = MagicMock()
    return logger


@pytest.fixture
def rbac_middleware(mock_supabase, mock_logger):
    """Create RBACMiddleware with mocked dependencies."""
    app = MagicMock()
    with patch(
        "apps.api.api.middleware.rbac_middleware.SupabaseManager",
        return_value=mock_supabase,
    ):
        with patch(
            "apps.api.api.middleware.rbac_middleware.LoggingController",
            return_value=mock_logger,
        ):
            middleware = RBACMiddleware(app, supabase=mock_supabase)
            middleware.logger = mock_logger
            return middleware


@pytest.fixture
def sample_user_id():
    """Sample user UUID."""
    return uuid.uuid4()


@pytest.fixture
def sample_feature_id():
    """Sample feature UUID."""
    return uuid.uuid4()


@pytest.fixture
def mock_request(sample_user_id):
    """Mock FastAPI request."""
    request = MagicMock()
    request.state = MagicMock()
    request.state.required_feature = None
    request.state.user_id = str(sample_user_id)
    request.state.correlation_id = "test-correlation"
    return request


class TestRBACMiddlewareDispatch:
    """Tests for RBAC middleware dispatch."""

    @pytest.mark.asyncio
    async def test_no_feature_required(
        self, rbac_middleware, mock_request
    ):
        """Test request without required feature passes through."""
        # Arrange
        mock_request.state.required_feature = None
        call_next = AsyncMock(return_value="response")

        # Act
        result = await rbac_middleware.dispatch(mock_request, call_next)

        # Assert
        assert result == "response"
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_missing_user_id_returns_401(
        self, rbac_middleware, mock_request
    ):
        """Test request without user_id returns 401."""
        # Arrange
        mock_request.state.required_feature = "test_feature"
        mock_request.state.user_id = None
        call_next = AsyncMock()

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await rbac_middleware.dispatch(mock_request, call_next)
        assert exc_info.value.status_code == 401


class TestFeatureAccessHierarchy:
    """Tests for hierarchical feature access checking."""

    @pytest.mark.asyncio
    async def test_access_via_user_grants(
        self, rbac_middleware, sample_user_id, sample_feature_id, mock_supabase
    ):
        """Test access granted via user_grants (priority 1)."""
        # Arrange
        mock_supabase.query_records.side_effect = [
            [{"id": str(sample_feature_id), "key": "test_feature"}],  # Feature
            [
                {
                    "value": True,
                    "expires_at": None,
                    "feature_id": str(sample_feature_id),
                }
            ],  # Grant
        ]

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert has_access is True
        assert value is True

    @pytest.mark.asyncio
    async def test_access_via_user_groups(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test access granted via user_group_features (priority 2)."""
        # Arrange
        mock_supabase.query_records.side_effect = [
            [{"id": str(sample_feature_id), "key": "test_feature"}],  # Feature
            [],  # No grants
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[{"value": True}])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert has_access is True
        assert value is True

    @pytest.mark.asyncio
    async def test_access_via_plan_features(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test access granted via plan_features (priority 3)."""
        # Arrange
        plan_id = str(uuid.uuid4())
        mock_supabase.query_records.side_effect = [
            [{"id": str(sample_feature_id), "key": "test_feature"}],  # Feature
            [],  # No grants
            [
                {
                    "plan_id": plan_id,
                    "status": "active",
                    "created_at": "2024-01-01T00:00:00Z",
                }
            ],  # Subscription
            [{"value": True, "feature_id": str(sample_feature_id)}],  # Plan feature
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert has_access is True
        assert value is True

    @pytest.mark.asyncio
    async def test_access_via_default_value_true(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test access granted via default_value=true (priority 4)."""
        # Arrange
        mock_supabase.query_records.side_effect = [
            [
                {
                    "id": str(sample_feature_id),
                    "key": "test_feature",
                    "default_value": True,
                }
            ],  # Feature with default
            [],  # No grants
            [],  # No subscriptions
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert has_access is True
        assert value is True

    @pytest.mark.asyncio
    async def test_access_denied_default_false(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test access denied when default_value=false."""
        # Arrange
        mock_supabase.query_records.side_effect = [
            [
                {
                    "id": str(sample_feature_id),
                    "key": "test_feature",
                    "default_value": False,
                }
            ],  # Feature with default
            [],  # No grants
            [],  # No subscriptions
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert has_access is False
        assert value is False

    @pytest.mark.asyncio
    async def test_access_denied_no_grant(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test access denied when no grant found at any level."""
        # Arrange
        mock_supabase.query_records.side_effect = [
            [
                {
                    "id": str(sample_feature_id),
                    "key": "test_feature",
                    "default_value": None,
                }
            ],  # Feature with no default
            [],  # No grants
            [],  # No subscriptions
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        assert has_access is False
        assert value is None

    @pytest.mark.asyncio
    async def test_expired_grant_falls_through(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test expired grant is ignored and falls through to next level."""
        # Arrange — the grant below carries a hardcoded past expiry.
        mock_supabase.query_records.side_effect = [
            [{"id": str(sample_feature_id), "key": "test_feature"}],  # Feature
            [
                {
                    "value": True,
                    "expires_at": "2020-01-01T00:00:00+00:00",  # Expired
                    "feature_id": str(sample_feature_id),
                }
            ],  # Expired grant
            [],  # No subscriptions
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[{"value": True}])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_feature", "test-correlation"
        )

        # Assert
        # Should fall through to groups (priority 2)
        assert has_access is True
        assert value is True


class TestFeatureAccessWithQuotas:
    """Tests for feature access with quota values."""

    @pytest.mark.asyncio
    async def test_quota_value_via_groups(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test quota value access via groups."""
        # Arrange
        mock_supabase.query_records.side_effect = [
            [{"id": str(sample_feature_id), "key": "test_quota"}],  # Feature
            [],  # No grants
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[{"value": 1000}])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_quota", "test-correlation"
        )

        # Assert
        assert has_access is True
        assert value == 1000

    @pytest.mark.asyncio
    async def test_config_value_via_groups(
        self,
        rbac_middleware,
        sample_user_id,
        sample_feature_id,
        mock_supabase,
    ):
        """Test config dict value access via groups."""
        # Arrange
        config_value = {"max_items": 100, "priority": "high"}
        mock_supabase.query_records.side_effect = [
            [{"id": str(sample_feature_id), "key": "test_config"}],  # Feature
            [],  # No grants
        ]
        mock_rpc = MagicMock()
        mock_rpc.execute.return_value = MagicMock(data=[{"value": config_value}])
        mock_supabase.client.rpc.return_value = mock_rpc

        # Act
        has_access, value = await rbac_middleware._check_feature_access(
            sample_user_id, "test_config", "test-correlation"
        )

        # Assert
        assert has_access is True
        assert value == config_value
