"""Centralized environment-aware configuration."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def read_secret(secret_name: str, default: str | None = None) -> str | None:
    """Read secret from Docker secrets file if available.

    Docker Compose mounts secrets at /run/secrets/<secret_name>.
    Falls back to environment variable if secret file doesn't exist.

    Args:
        secret_name: Name of the secret
        default: Default value if neither secret file nor env var exists

    Returns:
        Secret value from file, environment variable, or default
    """
    secret_path = Path(f"/run/secrets/{secret_name}")

    # Try Docker secret file first
    if secret_path.exists():
        try:
            return secret_path.read_text().strip()
        except Exception:
            pass

    # Fall back to environment variable
    return os.getenv(secret_name.upper(), default)


class Settings(BaseSettings):
    """Centralized environment-aware configuration.

    Configuration is loaded from environment variables and .env files.
    Environment-specific files (.env.development, .env.production) override
    shared settings (.env.shared).

    Compliance:
    - BLOC-008: Log masking enforcement in production
    - BLOC-004: Separate .env files per environment
    - BLOC-009: External secrets manager for production
    - se.architecture.rules.md BLOC-ARCH-005: Centralized configuration
    """

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Environment
    app_env: Literal["development", "production"] = Field(
        default="development",
        description="Application environment"
    )
    environment: str = Field(
        default="development",
        description="Legacy environment variable for compatibility"
    )
    app_version: str = Field(default="1.0.0", description="Application version")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    disable_log_masking: bool = Field(
        default=False,
        description="Disable log masking (FORBIDDEN in production - BLOC-008)"
    )
    log_retention_days: int = Field(
        default=7,
        description="Number of days to retain logs"
    )

    # Security
    enable_rate_limiting: bool = Field(
        default=True,
        description="Enable rate limiting middleware"
    )
    request_timeout_seconds: int = Field(
        default=30,
        description="Maximum request timeout in seconds"
    )
    max_request_size_mb: int = Field(
        default=10,
        description="Maximum request body size in megabytes"
    )

    # Domains
    api_domain: str = Field(
        default="api.dev.example.local",
        description="API domain name"
    )
    worker_domain: str = Field(
        default="worker.dev.example.local",
        description="Worker domain name"
    )
    api_url: str = Field(
        default="http://localhost:8002",
        description="Full API URL"
    )

    # CORS
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:8000",
        description="Comma-separated list of allowed CORS origins"
    )

    # Rate limiting
    rate_limit_free_plan: int = Field(
        default=100,
        description="Rate limit for free plan (requests per window)"
    )
    rate_limit_pro_plan: int = Field(
        default=1000,
        description="Rate limit for pro plan (requests per window)"
    )
    rate_limit_enterprise_plan: int = Field(
        default=10000,
        description="Rate limit for enterprise plan (requests per window)"
    )
    rate_limit_unauthenticated: int = Field(
        default=20,
        description="Rate limit for unauthenticated requests (requests per window)"
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        description="Rate limit window duration in seconds"
    )

    # Supabase
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_anon_key: str = Field(..., description="Supabase anonymous key")
    supabase_service_key: str | None = Field(
        default=None,
        description="Supabase service role key"
    )

    # Redis
    redis_host: str = Field(default="localhost", description="Redis host")
    redis_port: int = Field(default=6379, description="Redis port")
    redis_password: str = Field(default="", description="Redis password")
    redis_db: int = Field(default=0, description="Redis database number")
    redis_url: str | None = Field(
        default=None,
        description="Complete Redis URL (overrides individual settings)"
    )

    # Observability
    enable_tracing: bool = Field(
        default=False,
        description="Enable distributed tracing"
    )
    otel_sampling_rate: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="OpenTelemetry sampling rate (0.0 to 1.0)"
    )
    otel_service_name: str = Field(
        default="saas-api",
        description="Service name for tracing"
    )
    otel_exporter_type: str = Field(
        default="jaeger",
        description="Exporter type (jaeger or otlp)"
    )
    jaeger_agent_host: str = Field(
        default="localhost",
        description="Jaeger agent host"
    )
    jaeger_agent_port: int = Field(
        default=6831,
        description="Jaeger agent port"
    )

    # Secrets management (simple .env file approach)
    # For production: Use encrypted .env files (git-crypt) or environment variables
    # injected by your deployment platform (Docker secrets, Kubernetes secrets, etc.)

    # Circuit breakers
    enable_circuit_breakers: bool = Field(
        default=True,
        description="Enable circuit breaker protection"
    )
    supabase_circuit_failure_threshold: int = Field(
        default=5,
        description="Supabase circuit breaker failure threshold"
    )
    supabase_circuit_timeout: int = Field(
        default=60,
        description="Supabase circuit breaker timeout in seconds"
    )
    redis_circuit_failure_threshold: int = Field(
        default=5,
        description="Redis circuit breaker failure threshold"
    )
    redis_circuit_timeout: int = Field(
        default=30,
        description="Redis circuit breaker timeout in seconds"
    )
    webhook_circuit_failure_threshold: int = Field(
        default=5,
        description="Webhook circuit breaker failure threshold"
    )
    webhook_circuit_timeout: int = Field(
        default=60,
        description="Webhook circuit breaker timeout in seconds"
    )

    # Graceful shutdown
    shutdown_timeout: int = Field(
        default=30,
        description="Maximum seconds to wait for graceful shutdown"
    )

    # Email (optional, for production)
    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP host")
    smtp_port: int = Field(default=587, description="SMTP port")
    smtp_use_tls: bool = Field(default=True, description="Use TLS for SMTP")
    smtp_username: str | None = Field(default=None, description="SMTP username")
    smtp_password: str | None = Field(default=None, description="SMTP password")
    smtp_from_email: str | None = Field(
        default=None,
        description="From email address"
    )
    smtp_from_name: str | None = Field(default=None, description="From name")

    # Webhooks
    webhook_timeout: int = Field(
        default=30,
        description="Webhook request timeout in seconds"
    )
    webhook_max_retries: int = Field(
        default=3,
        description="Maximum webhook retry attempts"
    )
    webhook_retry_delay: int = Field(
        default=5,
        description="Delay between webhook retries in seconds"
    )

    # Notifications
    notification_default_expiry_days: int = Field(
        default=30,
        description="Default notification expiry in days"
    )

    # Subscriptions
    default_trial_period_days: int = Field(
        default=14,
        description="Default trial period in days"
    )

    @field_validator("app_env")
    @classmethod
    def validate_app_env(cls, v: str) -> str:
        """Validate app_env is either development or production."""
        if v not in ["development", "production"]:
            raise ValueError(
                f"app_env must be 'development' or 'production', got: {v}"
            )
        return v

    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env == "production"

    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.app_env == "development"

    def validate_production_constraints(self) -> None:
        """Validate production-specific rules.

        Enforces:
        - BLOC-008: Log masking must be enabled in production

        Raises:
            ValueError: If production constraints are violated
        """
        if self.is_production() and self.disable_log_masking:
            raise ValueError(
                "DISABLE_LOG_MASKING=true is forbidden in production "
                "(BLOC-008 compliance: log masking is required in production)"
            )

    def get_cors_origins_list(self) -> list[str]:
        """Get CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @model_validator(mode="after")
    def load_secrets_from_docker(self) -> "Settings":
        """Load secrets from environment variables or Docker secrets files.

        Priority: Environment variables > Docker secrets > defaults
        This ensures compatibility with both Docker Compose secrets and
        platform injection (Dokploy, Kubernetes, etc.)
        """
        # Supabase URL - environment variable takes priority
        if not self.supabase_url or self.supabase_url.startswith("http"):
            # Only try Docker secret if env var is not set or looks like a placeholder
            if secret_url := read_secret("supabase_url"):
                self.supabase_url = secret_url

        # Supabase Anon Key - environment variable takes priority
        if not self.supabase_anon_key or len(self.supabase_anon_key) < 20:
            # Only try Docker secret if env var is not set or looks invalid
            if secret_anon_key := read_secret("supabase_anon_key"):
                self.supabase_anon_key = secret_anon_key

        # Supabase Service Key - environment variable takes priority
        if not self.supabase_service_key or len(self.supabase_service_key) < 20:
            # Only try Docker secret if env var is not set or looks invalid
            if secret_service_key := read_secret("supabase_service_key"):
                self.supabase_service_key = secret_service_key

        return self


@lru_cache
def get_settings() -> Settings:
    """Get singleton settings instance.

    Settings are cached after first load. Call this function to access
    application configuration throughout the codebase.

    Returns:
        Settings: Singleton settings instance

    Raises:
        ValueError: If production constraints are violated
    """
    settings = Settings()
    settings.validate_production_constraints()
    return settings
