"""Unit tests for WebhookService."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.api.api.exceptions.domain_exceptions import NotFoundError, ValidationError
from apps.api.api.models.webhook.webhook_models import (
    WebhookCreate,
    WebhookUpdate,
)
from apps.api.api.services.webhook.webhook_service import WebhookService


@pytest.fixture
def mock_supabase():
    """Mock SupabaseManager for testing."""
    supabase = MagicMock()
    supabase.insert_record = MagicMock()
    supabase.get_record = MagicMock()
    supabase.update_record = MagicMock()
    supabase.delete_record = MagicMock()
    supabase.client = MagicMock()
    return supabase


@pytest.fixture
def mock_redis():
    """Mock RedisManager for testing."""
    redis = MagicMock()
    redis.push_to_queue = AsyncMock()
    redis.pop_from_queue = AsyncMock()
    return redis


@pytest.fixture
def mock_logger():
    """Mock LoggingController for testing."""
    logger = MagicMock()
    return logger


@pytest.fixture
def webhook_service(mock_supabase, mock_redis, mock_logger):
    """Create WebhookService with mocked dependencies."""
    return WebhookService(
        supabase=mock_supabase,
        redis=mock_redis,
        logger=mock_logger,
    )


@pytest.fixture
def sample_user_id():
    """Sample user UUID."""
    return uuid.uuid4()


@pytest.fixture
def sample_webhook_id():
    """Sample webhook UUID."""
    return uuid.uuid4()


class TestCreateWebhook:
    """Tests for creating webhooks."""

    @pytest.mark.asyncio
    async def test_create_webhook_success(
        self, webhook_service, sample_user_id, mock_supabase
    ):
        """Test successful webhook creation."""
        # Arrange
        webhook_data = WebhookCreate(
            url="https://example.com/webhook",
            events=["user.created", "user.updated"],
            active=True,
        )

        mock_supabase.insert_record.return_value = {
            "id": str(uuid.uuid4()),
            "user_id": str(sample_user_id),
            "url": "https://example.com/webhook",
            "secret": "generated_secret",
            "events": ["user.created", "user.updated"],
            "active": True,
            "retry_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        # Act
        result = await webhook_service.create_webhook(
            user_id=sample_user_id,
            data=webhook_data,
            correlation_id="test-correlation",
        )

        # Assert
        assert result.url == "https://example.com/webhook"
        assert result.events == ["user.created", "user.updated"]
        assert result.active is True
        mock_supabase.insert_record.assert_called_once()


class TestGetWebhook:
    """Tests for getting webhooks."""

    @pytest.mark.asyncio
    async def test_get_webhook_success(
        self, webhook_service, sample_user_id, sample_webhook_id, mock_supabase
    ):
        """Test successful webhook retrieval."""
        # Arrange
        mock_supabase.get_record.return_value = {
            "id": str(sample_webhook_id),
            "user_id": str(sample_user_id),
            "url": "https://example.com/webhook",
            "secret": "secret",
            "events": ["user.created"],
            "active": True,
            "retry_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        # Act
        result = await webhook_service.get_webhook(
            webhook_id=sample_webhook_id,
            user_id=sample_user_id,
            correlation_id="test-correlation",
        )

        # Assert
        assert result.id == sample_webhook_id
        assert result.url == "https://example.com/webhook"

    @pytest.mark.asyncio
    async def test_get_webhook_not_found(
        self, webhook_service, sample_user_id, sample_webhook_id, mock_supabase
    ):
        """Test webhook not found."""
        # Arrange
        mock_supabase.get_record.return_value = None

        # Act & Assert
        with pytest.raises(NotFoundError):
            await webhook_service.get_webhook(
                webhook_id=sample_webhook_id,
                user_id=sample_user_id,
                correlation_id="test-correlation",
            )

    @pytest.mark.asyncio
    async def test_get_webhook_wrong_user(
        self, webhook_service, sample_webhook_id, mock_supabase
    ):
        """Test webhook belongs to different user."""
        # Arrange
        owner_user_id = uuid.uuid4()
        requester_user_id = uuid.uuid4()

        mock_supabase.get_record.return_value = {
            "id": str(sample_webhook_id),
            "user_id": str(owner_user_id),  # Different user
            "url": "https://example.com/webhook",
            "secret": "secret",
            "events": ["user.created"],
            "active": True,
            "retry_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        # Act & Assert
        with pytest.raises(NotFoundError):
            await webhook_service.get_webhook(
                webhook_id=sample_webhook_id,
                user_id=requester_user_id,  # Different user
                correlation_id="test-correlation",
            )


class TestUpdateWebhook:
    """Tests for updating webhooks."""

    @pytest.mark.asyncio
    async def test_update_webhook_success(
        self, webhook_service, sample_user_id, sample_webhook_id, mock_supabase
    ):
        """Test successful webhook update."""
        # Arrange
        existing_webhook = {
            "id": str(sample_webhook_id),
            "user_id": str(sample_user_id),
            "url": "https://example.com/webhook",
            "secret": "secret",
            "events": ["user.created"],
            "active": True,
            "retry_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_supabase.get_record.return_value = existing_webhook
        mock_supabase.update_record.return_value = {
            **existing_webhook,
            "url": "https://new.example.com/webhook",
            "active": False,
        }

        update_data = WebhookUpdate(
            url="https://new.example.com/webhook",
            active=False,
        )

        # Act
        result = await webhook_service.update_webhook(
            webhook_id=sample_webhook_id,
            user_id=sample_user_id,
            data=update_data,
            correlation_id="test-correlation",
        )

        # Assert
        assert result.url == "https://new.example.com/webhook"
        assert result.active is False


class TestDeleteWebhook:
    """Tests for deleting webhooks."""

    @pytest.mark.asyncio
    async def test_delete_webhook_success(
        self, webhook_service, sample_user_id, sample_webhook_id, mock_supabase
    ):
        """Test successful webhook deletion."""
        # Arrange
        mock_supabase.get_record.return_value = {
            "id": str(sample_webhook_id),
            "user_id": str(sample_user_id),
            "url": "https://example.com/webhook",
            "secret": "secret",
            "events": ["user.created"],
            "active": True,
            "retry_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        # Act
        result = await webhook_service.delete_webhook(
            webhook_id=sample_webhook_id,
            user_id=sample_user_id,
            correlation_id="test-correlation",
        )

        # Assert
        assert result is True
        mock_supabase.delete_record.assert_called_once()


class TestTriggerWebhook:
    """Tests for triggering webhooks."""

    @pytest.mark.asyncio
    async def test_trigger_webhook_enqueues_jobs(
        self, webhook_service, mock_supabase, mock_redis
    ):
        """Test that trigger_webhook enqueues jobs for matching webhooks,
        decrypting the at-rest secret (vuln-0010, pinned) back to its raw
        value for the outbound-signing job — the row itself never holds
        plaintext."""
        # Arrange
        from apps.api.api.services.webhook.webhook_secret_crypto import encrypt_secret

        webhook_id_1 = str(uuid.uuid4())
        webhook_id_2 = str(uuid.uuid4())

        mock_response = MagicMock()
        mock_response.data = [
            {
                "id": webhook_id_1,
                "url": "https://example1.com/webhook",
                "secret": encrypt_secret("secret1"),
                "events": ["user.created"],
            },
            {
                "id": webhook_id_2,
                "url": "https://example2.com/webhook",
                "secret": encrypt_secret("secret2"),
                "events": ["user.created"],
            },
        ]

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.contains.return_value = mock_query
        mock_query.execute.return_value = mock_response

        mock_supabase.client.table.return_value = mock_query

        # Act
        result = await webhook_service.trigger_webhook(
            event_name="user.created",
            data={"id": "user-123", "email": "test@example.com"},
            correlation_id="test-correlation",
        )

        # Assert
        assert result == 2  # Two webhooks enqueued
        assert mock_redis.push_to_queue.call_count == 2
        queued_secrets = {
            call.args[1]["secret"] for call in mock_redis.push_to_queue.call_args_list
        }
        assert queued_secrets == {"secret1", "secret2"}  # decrypted, not ciphertext

    @pytest.mark.asyncio
    async def test_trigger_webhook_no_matching_webhooks(
        self, webhook_service, mock_supabase, mock_redis
    ):
        """Test trigger_webhook with no matching webhooks."""
        # Arrange
        mock_response = MagicMock()
        mock_response.data = []

        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.contains.return_value = mock_query
        mock_query.execute.return_value = mock_response

        mock_supabase.client.table.return_value = mock_query

        # Act
        result = await webhook_service.trigger_webhook(
            event_name="unknown.event",
            data={},
            correlation_id="test-correlation",
        )

        # Assert
        assert result == 0
        mock_redis.push_to_queue.assert_not_called()


class TestSecretNeverReturnedPastCreation:
    """vuln-0010 (strix pilot bb1f35fc, pinned): the webhook signing secret
    is shown ONCE, on create (or regenerate) — get/list/update must not
    return it, and the row itself is never plaintext."""

    @pytest.mark.asyncio
    async def test_create_returns_raw_secret_but_stores_it_encrypted(
        self, webhook_service, sample_user_id, mock_supabase
    ):
        webhook_data = WebhookCreate(
            url="https://example.com/webhook", events=["user.created"], active=True
        )
        captured = {}

        def _insert_record(table, data, correlation_id=""):
            captured.update(data)
            return {
                "id": str(uuid.uuid4()),
                "user_id": str(sample_user_id),
                "url": data["url"],
                "secret": data["secret"],
                "events": data["events"],
                "active": data["active"],
                "retry_count": 0,
                "last_attempt_at": None,
                "last_success_at": None,
                "last_error": None,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }

        mock_supabase.insert_record.side_effect = _insert_record

        result = await webhook_service.create_webhook(
            user_id=sample_user_id, data=webhook_data, correlation_id="test"
        )

        assert result.secret is not None
        assert captured["secret"] != result.secret  # stored value is ciphertext

    @pytest.mark.asyncio
    async def test_get_list_update_redact_secret(
        self, webhook_service, sample_user_id, sample_webhook_id, mock_supabase
    ):
        row = {
            "id": str(sample_webhook_id),
            "user_id": str(sample_user_id),
            "url": "https://example.com/webhook",
            "secret": "ciphertext-not-a-real-fernet-token",
            "events": ["user.created"],
            "active": True,
            "retry_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_supabase.get_record.return_value = row

        got = await webhook_service.get_webhook(
            webhook_id=sample_webhook_id, user_id=sample_user_id, correlation_id="t"
        )
        assert got.secret is None

        mock_response = MagicMock()
        mock_response.count = 1
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.match.return_value = mock_query
        mock_query.order.return_value = mock_query
        mock_query.range.return_value = mock_query
        mock_query.execute.return_value = mock_response
        mock_query.execute.return_value.data = [row]
        mock_supabase.client.table.return_value = mock_query

        listed = await webhook_service.list_webhooks(
            user_id=sample_user_id, correlation_id="t"
        )
        assert all(item.secret is None for item in listed.items)

        mock_supabase.update_record.return_value = {**row, "active": False}
        updated = await webhook_service.update_webhook(
            webhook_id=sample_webhook_id,
            user_id=sample_user_id,
            data=WebhookUpdate(active=False),
            correlation_id="t",
        )
        assert updated.secret is None

    @pytest.mark.asyncio
    async def test_regenerate_secret_returns_new_raw_once(
        self, webhook_service, sample_user_id, sample_webhook_id, mock_supabase
    ):
        existing = {
            "id": str(sample_webhook_id),
            "user_id": str(sample_user_id),
            "url": "https://example.com/webhook",
            "secret": "old-ciphertext",
            "events": ["user.created"],
            "active": True,
            "retry_count": 0,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error": None,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_supabase.get_record.return_value = existing
        captured = {}

        def _update_record(table, wid, data, correlation_id=""):
            captured.update(data)
            return {**existing, **data}

        mock_supabase.update_record.side_effect = _update_record

        result = await webhook_service.regenerate_secret(
            webhook_id=sample_webhook_id, user_id=sample_user_id, correlation_id="t"
        )

        assert result.secret is not None
        assert result.secret != existing["secret"]
        assert captured["secret"] != result.secret  # stored value is ciphertext
