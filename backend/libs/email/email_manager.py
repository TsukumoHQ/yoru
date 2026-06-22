# Standard library
import asyncio

# Local
from libs.log_manager.controller import LoggingController
from libs.log_manager.core.utils import get_correlation_id
from libs.email.config import EmailConfig
from libs.email.exceptions import EmailSendError, EmailConfigError
from libs.email.providers.base import BaseEmailProvider, EmailMessage
from libs.email.providers.smtp import SMTPProvider
from libs.email.providers.sendgrid import SendGridProvider
from libs.email.providers.resend import ResendProvider
from libs.email.templates import TemplateManager


class EmailManager:
    """
    Main email service manager following RedisManager pattern.

    Handles email sending with templates, retry logic, and multi-provider support.
    Follows fail-safe pattern: email failures are logged but don't block operations.
    """

    def __init__(
        self,
        config: EmailConfig | None = None,
        logger: LoggingController | None = None,
    ) -> None:
        """
        Initialize email manager.

        Args:
            config: Email configuration (defaults to from_env)
            logger: Optional logger instance

        Raises:
            EmailConfigError: If configuration is invalid
        """
        self.config = config or EmailConfig.from_env()
        self.logger = logger or LoggingController(app_name="EmailManager")
        self.template_manager = TemplateManager(logger=self.logger)
        # No provider creds (self-host without SMTP) → run disabled: construction
        # succeeds and sends become logged no-ops, so services that build an
        # EmailManager in __init__ don't crash and the dashboard stays up.
        self.provider = None if self.config.disabled else self._initialize_provider()
        if self.config.disabled:
            self.logger.log_info(
                "EmailManager disabled (no provider configured) — sends are no-ops",
                {"component": "EmailManager"},
            )

        self.logger.log_info(
            "EmailManager initialized",
            {
                "component": "EmailManager",
                "provider": self.config.provider,
                "from_email": self.config.from_email,
                "retry_attempts": self.config.retry_attempts,
            },
        )

    def _initialize_provider(self) -> BaseEmailProvider:
        """
        Initialize email provider based on configuration.

        Returns:
            Configured email provider

        Raises:
            EmailConfigError: If provider type is unknown
        """
        if self.config.provider == "smtp":
            if not all(
                [
                    self.config.smtp_host,
                    self.config.smtp_username,
                    self.config.smtp_password,
                ]
            ):
                raise EmailConfigError("SMTP provider requires host, username, password")

            return SMTPProvider(
                host=self.config.smtp_host,  # type: ignore
                port=self.config.smtp_port or 587,
                username=self.config.smtp_username,  # type: ignore
                password=self.config.smtp_password,  # type: ignore
                use_tls=self.config.smtp_use_tls,
                timeout=self.config.timeout,
                logger=self.logger,
            )

        elif self.config.provider == "sendgrid":
            if not self.config.sendgrid_api_key:
                raise EmailConfigError("SendGrid provider requires SENDGRID_API_KEY")

            return SendGridProvider(
                api_key=self.config.sendgrid_api_key,
                timeout=self.config.timeout,
                logger=self.logger,
            )

        elif self.config.provider == "resend":
            if not self.config.resend_api_key:
                raise EmailConfigError("Resend provider requires RESEND_API_KEY")

            return ResendProvider(
                api_key=self.config.resend_api_key,
                timeout=self.config.timeout,
                logger=self.logger,
            )

        else:
            raise EmailConfigError(
                f"Unknown email provider: {self.config.provider}. "
                f"Supported: smtp, sendgrid, resend"
            )

    async def send_template(
        self,
        template_name: str,
        to_email: str,
        subject: str,
        context: dict[str, str] | None = None,
        correlation_id: str | None = None,
        to_name: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """
        Render and send an email template.

        Implements retry logic and fail-safe error handling.
        Follows python.async.rules.md for async patterns.

        Args:
            template_name: Name of template file (e.g., "invitation.html")
            to_email: Recipient email address
            subject: Email subject line
            context: Template variables
            correlation_id: Request correlation ID for tracing
            to_name: Recipient display name
            reply_to: Reply-to email address

        Returns:
            True if sent successfully after retries

        Raises:
            EmailSendError: If all retry attempts fail
            EmailTemplateError: If template rendering fails
        """
        if self.config.disabled:
            self.logger.log_info(
                "Email send skipped — EmailManager disabled (no SMTP configured)",
                {"component": "EmailManager", "to": to_email, "subject": subject},
            )
            return False

        if correlation_id is None:
            correlation_id = get_correlation_id()

        if context is None:
            context = {}

        # Add global template context
        template_context = {
            **context,
            "brand_name": self.config.brand_name,
            "support_email": self.config.support_email,
            "company_address": self.config.company_address,
        }

        log_context = {
            "operation": "send_templated_email",
            "component": "EmailManager",
            "correlation_id": correlation_id,
            "template_name": template_name,
            "to_email": to_email,
            "subject": subject[:50],
        }

        self.logger.log_info("Sending templated email", log_context)

        # Render template
        try:
            html_body = self.template_manager.render(
                template_name=template_name,
                context=template_context,
            )
        except Exception as e:
            # Template errors are not retryable
            self.logger.log_error(
                "Template rendering failed",
                {**log_context, "error": str(e)},
            )
            raise

        # Create email message
        message = EmailMessage(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            to_name=to_name,
            reply_to=reply_to,
        )

        # Retry loop
        last_error: Exception | None = None

        for attempt in range(1, self.config.retry_attempts + 1):
            attempt_context = {
                **log_context,
                "attempt": attempt,
                "max_attempts": self.config.retry_attempts,
            }

            try:
                await self.provider.send(
                    message=message,
                    from_email=self.config.from_email,
                    from_name=self.config.from_name,
                )

                self.logger.log_info(
                    "Email sent successfully",
                    {**attempt_context, "final_attempt": attempt},
                )

                return True

            except EmailSendError as e:
                last_error = e
                self.logger.log_warning(
                    f"Email send attempt {attempt} failed",
                    {
                        **attempt_context,
                        "error": str(e),
                        "will_retry": attempt < self.config.retry_attempts,
                    },
                )

                # BLOC-005: Use asyncio.sleep for delay between retries
                if attempt < self.config.retry_attempts:
                    delay = 2**attempt  # Exponential backoff: 2s, 4s, 8s
                    await asyncio.sleep(delay)

        # All retries exhausted
        self.logger.log_error(
            "All email send attempts failed",
            {
                **log_context,
                "total_attempts": self.config.retry_attempts,
                "final_error": str(last_error) if last_error else "Unknown",
            },
        )

        # STRONG-003: Chain exception
        raise EmailSendError(
            f"Failed to send email after {self.config.retry_attempts} attempts",
            correlation_id,
        ) from last_error

    async def send_simple(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        correlation_id: str | None = None,
        to_name: str | None = None,
        reply_to: str | None = None,
    ) -> bool:
        """
        Send a simple email without template rendering.

        Useful for dynamic content or testing.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            html_body: Pre-rendered HTML content
            correlation_id: Request correlation ID
            to_name: Recipient display name
            reply_to: Reply-to email address

        Returns:
            True if sent successfully

        Raises:
            EmailSendError: If sending fails after retries
        """
        if self.config.disabled:
            self.logger.log_info(
                "Email send skipped — EmailManager disabled (no SMTP configured)",
                {"component": "EmailManager", "to": to_email, "subject": subject},
            )
            return False

        if correlation_id is None:
            correlation_id = get_correlation_id()

        log_context = {
            "operation": "send_simple_email",
            "component": "EmailManager",
            "correlation_id": correlation_id,
            "to_email": to_email,
            "subject": subject[:50],
        }

        self.logger.log_info("Sending simple email", log_context)

        message = EmailMessage(
            to_email=to_email,
            subject=subject,
            html_body=html_body,
            to_name=to_name,
            reply_to=reply_to,
        )

        # Retry loop
        last_error: Exception | None = None

        for attempt in range(1, self.config.retry_attempts + 1):
            try:
                await self.provider.send(
                    message=message,
                    from_email=self.config.from_email,
                    from_name=self.config.from_name,
                )

                self.logger.log_info("Simple email sent successfully", log_context)
                return True

            except EmailSendError as e:
                last_error = e
                if attempt < self.config.retry_attempts:
                    delay = 2**attempt
                    await asyncio.sleep(delay)

        # All retries failed
        raise EmailSendError(
            f"Failed to send email after {self.config.retry_attempts} attempts",
            correlation_id,
        ) from last_error

    async def verify_configuration(self) -> bool:
        """
        Verify email provider configuration.

        Returns:
            True if configuration is valid and connection works
        """
        correlation_id = get_correlation_id()

        self.logger.log_info(
            "Verifying email configuration",
            {
                "component": "EmailManager",
                "correlation_id": correlation_id,
                "provider": self.config.provider,
            },
        )

        result = await self.provider.verify_configuration()

        if result:
            self.logger.log_info("Email configuration verified successfully", {})
        else:
            self.logger.log_warning("Email configuration verification failed", {})

        return result
