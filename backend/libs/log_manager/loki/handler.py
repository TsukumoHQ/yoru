import logging
import os
import json
import socket
import time
import requests
from .formatter import LokiJsonFormatter


class LokiHandler(logging.Handler):
    """
    Custom logging handler that sends logs to Grafana Loki
    """

    def __init__(
        self,
        loki_url=None,
        hostname=None,
        app_name=None,
        buffer_size=10,
        max_retry=3,
        retry_interval=1,
        use_json=True,
    ):
        super().__init__()

        # Auto-detect if we're running in a container
        in_container = os.path.exists("/.dockerenv")

        # Use provided URL if set, otherwise set based on environment
        if loki_url:
            self.loki_url = loki_url
        else:
            # When running locally, use localhost instead of 'loki'
            default_url = os.getenv(
                "LOKI_URL", "http://yoru_loki:3100/loki/api/v1/push"
            )
            if not in_container and "loki:" in default_url:
                # Replace 'loki' with 'localhost' for non-container environments
                default_url = default_url.replace("loki:", "localhost:")
            self.loki_url = default_url

        self.hostname = hostname or socket.gethostname()
        self.app_name = app_name or "yoru"
        self.buffer = []
        self.buffer_size = buffer_size
        self.max_retry = max_retry
        self.retry_interval = retry_interval
        self.use_json = use_json
        self.last_connection_error_time = 0
        self.connection_error_threshold = (
            60  # Only log connection errors once per minute
        )

        # Set formatter based on use_json flag
        if self.use_json:
            self.setFormatter(LokiJsonFormatter())

        # Log initialization (debug level to reduce verbosity)
        # print(f"Loki handler initialized with URL: {self.loki_url}")

    def emit(self, record):
        try:
            # Get extra from record if it exists
            extra = getattr(record, "extra", {}) if hasattr(record, "extra") else {}

            # Format timestamp for Loki (nanoseconds since epoch)
            timestamp = int(record.created * 1_000_000_000)

            # Create base stream labels
            stream_labels = {
                "app": self.app_name,
                "host": self.hostname,
                "level": record.levelname,
                "module": record.name,
            }

            # Add correlation ID if present
            if "correlation_id" in extra:
                stream_labels["correlation_id"] = extra["correlation_id"]

            # Create log entry
            log_entry = {
                "stream": stream_labels,
                "values": [[str(timestamp), self.format(record)]],
            }

            # Add the log to buffer
            self.buffer.append(log_entry)

            # Send logs when buffer reaches desired size
            if len(self.buffer) >= self.buffer_size:
                self.flush()

        except Exception as e:
            # Avoid infinite recursion if there's an error in the handler
            current_time = time.time()
            if (
                current_time - self.last_connection_error_time
                > self.connection_error_threshold
            ):
                print(f"Error in LokiHandler: {e}")
                self.last_connection_error_time = current_time

    def flush(self):
        if not self.buffer:
            return

        # Make a copy of the buffer and clear it
        buffer_to_send = self.buffer.copy()
        self.buffer = []

        retry_count = 0
        success = False

        while not success and retry_count < self.max_retry:
            try:
                # Prepare payload for Loki
                payload = {"streams": buffer_to_send}

                # Send logs to Loki
                response = requests.post(
                    self.loki_url,
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload),
                    timeout=5,  # Increased timeout for reliability
                )

                # Check response
                if response.status_code == 204 or response.status_code == 200:
                    success = True
                else:
                    # Log error but don't retry on client errors (4xx)
                    current_time = time.time()
                    if (
                        current_time - self.last_connection_error_time
                        > self.connection_error_threshold
                    ):
                        print(
                            f"Failed to send logs to Loki: {response.status_code} {response.text}"
                        )
                        self.last_connection_error_time = current_time

                    if response.status_code >= 500:  # Only retry on server errors (5xx)
                        retry_count += 1
                        time.sleep(self.retry_interval)
                    else:
                        break  # Don't retry on client errors

            except (requests.RequestException, ConnectionError) as e:
                # Connection error, retry
                retry_count += 1

                # Log the error (but not too frequently)
                current_time = time.time()
                if (
                    current_time - self.last_connection_error_time
                    > self.connection_error_threshold
                ):
                    print(
                        f"Error connecting to Loki (attempt {retry_count}/{self.max_retry}): {e}"
                    )
                    self.last_connection_error_time = current_time

                time.sleep(self.retry_interval)

        # If we failed after all retries, keep logs in buffer for next flush
        if not success:
            # Prepend failed messages back to buffer (up to a limit to avoid memory issues)
            max_buffer_size = 1000
            self.buffer = buffer_to_send + self.buffer
            if len(self.buffer) > max_buffer_size:
                self.buffer = self.buffer[
                    -max_buffer_size:
                ]  # Keep only the most recent logs

    def close(self):
        """Make sure all logs are sent when the handler is closed"""
        self.flush()
        super().close()
