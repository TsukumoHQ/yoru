# Standard library
import os
from dataclasses import dataclass

# Local
from libs.email.exceptions import EmailConfigError


@dataclass
class EmailConfig:
    """Configuration for email service following RedisManager pattern."""

    provider: str  # smtp, sendgrid, resend
    from_email: str
    from_name: str
    brand_name: str
    support_email: str
    company_address: str
    retry_attempts: int
    timeout: float

    # When True, no SMTP/provider creds were configured — sends become no-ops.
    # Lets a self-hosted instance run (and invite/notify in-app) without email.
    disabled: bool = False

    # SMTP settings
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True

    # SendGrid settings
    sendgrid_api_key: str | None = None

    # Resend settings
    resend_api_key: str | None = None

    @classmethod
    def from_env(cls) -> "EmailConfig":
        """
        Create EmailConfig from environment variables.

        Follows libs/redis pattern for ENV-based initialization.

        Raises:
            EmailConfigError: If required variables are missing.
        """
        provider = os.getenv("EMAIL_PROVIDER", "smtp")
        from_email = os.getenv("SMTP_FROM_EMAIL", "")
        from_name = os.getenv("SMTP_FROM_NAME", "")

        # Detect whether real provider creds exist. If not, run DISABLED instead
        # of raising — a self-hosted instance must boot and operate (invitations,
        # notifications surface in-app) without configuring SMTP. The hosted
        # deployment sets the creds and runs normally.
        provider_ready = bool(from_email) and (
            (provider == "smtp" and all(
                [os.getenv("SMTP_HOST"), os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD")]
            ))
            or (provider == "sendgrid" and os.getenv("SENDGRID_API_KEY"))
            or (provider == "resend" and os.getenv("RESEND_API_KEY"))
        )

        return cls(
            disabled=not provider_ready,
            provider=provider,
            from_email=from_email,
            from_name=from_name,
            brand_name=os.getenv("EMAIL_BRAND_NAME", "Yoru"),
            support_email=os.getenv("EMAIL_SUPPORT_EMAIL", from_email),
            company_address=os.getenv("EMAIL_COMPANY_ADDRESS", ""),
            retry_attempts=int(os.getenv("EMAIL_RETRY_ATTEMPTS", "3")),
            timeout=float(os.getenv("EMAIL_TIMEOUT", "30.0")),
            # SMTP
            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME"),
            smtp_password=os.getenv("SMTP_PASSWORD"),
            smtp_use_tls=os.getenv("SMTP_USE_TLS", "true").lower() == "true",
            # SendGrid
            sendgrid_api_key=os.getenv("SENDGRID_API_KEY"),
            # Resend
            resend_api_key=os.getenv("RESEND_API_KEY"),
        )
