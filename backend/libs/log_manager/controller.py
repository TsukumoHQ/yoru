import logging
import os
import traceback
import json
import datetime
import socket
from logging.handlers import TimedRotatingFileHandler

from .core.json_formatter import JsonFormatter
from .core.file_formatter import FileFormatter
from .core.utils import (
    mask_sensitive_data,
    mask_sensitive_context,
    get_correlation_id,
    set_correlation_id,
    correlation_scope,
    reset_correlation_id,
)
# NOTE: LokiHandler import removed - logs go through Promtail scraping
# from .loki import LokiHandler


class LoggingController:
    """
    Main controller for application logging that manages different log outputs
    and provides a unified API for logging messages with context
    """

    def __init__(
        self,
        app_name: str,
        log_level: str = "INFO",
        log_dir: str = "logs",
        rotation_when: str = "midnight",
        interval: int = 1,
        backup_count: int = 7,
        use_loki: bool = None,
        loki_url: str = None,
        use_teams_webhook: bool = None,
        teams_webhook_url: str = None,
    ):
        """
        Initializes the LoggingController with application-relative log path
        """
        # Determine service category from app_name
        service_category = self._get_service_category(app_name)

        # Create logs directory in the current working directory with proper permissions
        base_log_dir = os.path.join(os.getcwd(), log_dir)

        # Create a service-specific subdirectory for logs
        service_log_dir = os.path.join(base_log_dir, service_category)

        try:
            os.makedirs(service_log_dir, mode=0o755, exist_ok=True)
        except PermissionError:
            # If we can't create in current directory, try user's home directory
            home_dir = os.path.expanduser("~")
            service_log_dir = os.path.join(
                home_dir, ".yoru", "logs", service_category
            )
            os.makedirs(service_log_dir, mode=0o755, exist_ok=True)

        # Use the service-specific path for the log file
        log_file = os.path.join(service_log_dir, f"{app_name}.log")

        self.app_name = app_name
        self.logger = logging.getLogger(app_name)

        # Clear any existing handlers
        self.logger.handlers = []
        
        # Prevent propagation to root logger to avoid duplicate logs
        self.logger.propagate = False

        # Set log level based on environment or default to INFO
        env_level = os.getenv("LOG_LEVEL", log_level).upper()
        numeric_level = getattr(logging, env_level, logging.INFO)
        self.logger.setLevel(numeric_level)

        # Create formatters
        console_formatter = JsonFormatter()
        file_formatter = FileFormatter()

        # File handler with rotation
        file_handler = TimedRotatingFileHandler(
            log_file,
            when=rotation_when,
            interval=interval,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(numeric_level)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(numeric_level)

        # Add handlers to logger
        #self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        # NOTE: Loki direct push is DISABLED by design.
        # All logs go through Promtail scraping stdout/stderr → Loki → Grafana
        # This avoids duplicate logs and simplifies the architecture.

        # Check if we should use Teams webhook for error notifications
        if use_teams_webhook is None:
            use_teams_webhook = (
                os.getenv("USE_TEAMS_WEBHOOK", "false").lower() == "true"
            )

        self.use_teams_webhook = use_teams_webhook
        self.teams_webhook_url = teams_webhook_url or os.getenv(
            "TEAMS_WEBHOOK_URL", None
        )

        # Lazy import to avoid circular imports
        if self.use_teams_webhook and self.teams_webhook_url:
            from app.libs.teams_webhook import TeamsWebhookManager

            self.teams_webhook_manager = TeamsWebhookManager(
                webhook_url=self.teams_webhook_url,
                app_name="LoggingController",
                log_level=log_level,
            )
            self.logger.info(
                f"Microsoft Teams webhook notifications enabled for errors/critical events"
            )
        else:
            self.teams_webhook_manager = None

        # Log initialization message
        self.logger.debug(f"Logging initialized. Log file: {log_file}")

    def _get_service_category(self, app_name):
        """
        Determine service category based on app_name
        This prevents log duplication by ensuring each app logs to a specific directory
        """
        # Check if app_name already contains a service identifier
        app_name_lower = app_name.lower()

        if "api" in app_name_lower or "router" in app_name_lower:
            return "api"
        elif "fireflies" in app_name_lower or "ingestor" in app_name_lower:
            return "fireflies"
        elif "qdrant" in app_name_lower and not "api" in app_name_lower:
            return "qdrant"
        else:
            # Default to api for unknown services
            return "api"

    def _log_with_context(self, level: str, message: str, context: dict = None):
        if context is None:
            context = {}

        # Ensure correlation_id exists
        if "correlation_id" not in context:
            context["correlation_id"] = get_correlation_id()

        # Mask sensitive data
        message = mask_sensitive_data(message)

        # Mask sensitive fields in context
        masked_context, masked_fields = mask_sensitive_context(context)
        if masked_fields:
            try:
                masked_context = dict(masked_context)
                masked_context["masked"] = True
                masked_context["masked_fields_count"] = len(masked_fields)
            except Exception:
                pass
        context = masked_context

        # Get the logging method
        log_method = getattr(self.logger, level)

        # Set extra for current record
        old_factory = logging.getLogRecordFactory()

        def record_factory(*args, **kwargs):
            record = old_factory(*args, **kwargs)
            record.extra = context
            return record

        logging.setLogRecordFactory(record_factory)

        # Log with context
        log_method(message)

        # Restore original record factory
        logging.setLogRecordFactory(old_factory)

        # Send Teams webhook notification for errors and criticals
        if (
            self.use_teams_webhook
            and self.teams_webhook_manager
            and level.lower() in ("error", "critical")
        ):
            # Extract error type if available
            error_type = "Unknown Error"
            if "Type:" in message:
                error_parts = message.split("Type:", 1)
                if len(error_parts) > 1:
                    error_type_line = error_parts[1].split("\n", 1)[0].strip()
                    if error_type_line:
                        error_type = error_type_line

            # Send appropriate notification based on level
            if level.lower() == "error":
                self.teams_webhook_manager.send_error_notification(
                    app_name=self.app_name,
                    message=message,
                    error_type=error_type,
                    context=context,
                )
            elif level.lower() == "critical":
                self.teams_webhook_manager.send_critical_notification(
                    app_name=self.app_name,
                    message=message,
                    error_type=error_type,
                    context=context,
                )

    def set_log_level(self, level: str):
        """Set the log level for all handlers"""
        numeric_level = getattr(logging, level.upper(), None)
        if isinstance(numeric_level, int):
            self.logger.setLevel(numeric_level)
            for handler in self.logger.handlers:
                handler.setLevel(numeric_level)
            self.log_info(f"Log level changed to {level.upper()}")
        else:
            self.log_error(f"Invalid log level: {level}")

    def get_correlation_id(self) -> str:
        """
        Get the current correlation ID for request tracing

        Returns:
            The current correlation ID string
        """
        return get_correlation_id()

    def set_correlation_id(self, correlation_id: str = None) -> str:
        """
        Set a specific correlation ID for the current thread

        Args:
            correlation_id: The correlation ID to set. If None, generates a new one.

        Returns:
            The correlation ID that was set
        """
        return set_correlation_id(correlation_id)

    def reset_correlation_id(self) -> None:
        """
        Reset the correlation ID for the current thread
        """
        reset_correlation_id()

    def correlation_scope(self, correlation_id: str = None):
        """
        Get a context manager to handle the correlation ID lifecycle

        Usage:
            with logger.correlation_scope():
                # All logging within this scope will use the same correlation ID
                logger.log_info("Processing request")
                process_request()

        Args:
            correlation_id: Optional custom correlation ID. If None, generates a new one.

        Returns:
            A context manager for correlation ID scope
        """
        return correlation_scope(correlation_id)

    def log_info(self, message: str, context: dict = None):
        self._log_with_context("info", message, context)

    def log_debug(self, message: str, context: dict = None):
        self._log_with_context("debug", message, context)

    def log_warning(self, message: str, context: dict = None):
        self._log_with_context("warning", message, context)

    def log_error(self, message: str, context: dict = None):
        self._log_with_context("error", message, context)

    def log_critical(self, message: str, context: dict = None):
        self._log_with_context("critical", message, context)

    def log_exception(self, ex: Exception, context: dict = None):
        """
        Log an exception with full traceback
        """
        if context is None:
            context = {}

        # Get exception details
        ex_type = type(ex).__name__
        ex_message = str(ex)
        ex_traceback = traceback.format_exc()

        # Create message with exception details
        message = f"Exception occurred.\nType: {ex_type}\nMessage: {ex_message}\nTraceback:\n{ex_traceback}"

        # Add exception info to context
        context.update({"exception_type": ex_type, "exception_message": ex_message})

        # Log with error level
        self._log_with_context("error", message, context)

    def is_debug_enabled(self) -> bool:
        """
        Check if DEBUG level logging is enabled.
        Useful for guarding expensive debug log formatting operations.
        
        Returns:
            True if DEBUG level logging is enabled, False otherwise
        """
        return self.logger.isEnabledFor(logging.DEBUG)
