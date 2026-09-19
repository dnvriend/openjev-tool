"""TelemetryService singleton for OpenTelemetry.

Provides centralized access to tracers, meters, and loggers.
Handles initialization and graceful shutdown of telemetry providers.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openjev_tool.telemetry.config import TelemetryConfig

if TYPE_CHECKING:
    from opentelemetry._logs import Logger as OTelLogger
    from opentelemetry.metrics import Meter
    from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)


class TelemetryService:
    """Singleton service for OpenTelemetry instrumentation.

    Provides centralized access to OpenTelemetry tracer and meter.
    Must be initialized before use with initialize().

    Example:
        >>> config = TelemetryConfig.from_env()
        >>> TelemetryService.get_instance().initialize(config)
        >>> tracer = TelemetryService.get_instance().tracer
        >>> with tracer.start_as_current_span("operation"):
        ...     pass
        >>> TelemetryService.get_instance().shutdown()
    """

    _instance: TelemetryService | None = None
    _initialized: bool = False

    def __new__(cls) -> TelemetryService:
        """Create singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def get_instance(cls) -> TelemetryService:
        """Get the singleton instance.

        Returns:
            The TelemetryService singleton instance.
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def initialize(self, config: TelemetryConfig) -> None:
        """Initialize telemetry with configuration.

        Sets up TracerProvider and MeterProvider with configured exporters.
        Safe to call multiple times; subsequent calls are no-ops.

        Args:
            config: Telemetry configuration.
        """
        if self._initialized:
            logger.debug("Telemetry already initialized, skipping")
            return

        self._config = config

        if not config.enabled:
            logger.debug("Telemetry disabled, using no-op providers")
            self._initialized = True
            return

        try:
            self._setup_providers(config)
            self._initialized = True
            logger.info(
                "Telemetry initialized: service=%s, exporter=%s",
                config.service_name,
                config.exporter_type.value,
            )
        except ImportError as e:
            logger.warning(
                "OpenTelemetry dependencies not installed, telemetry disabled: %s", e
            )
            self._config = TelemetryConfig(enabled=False)
            self._initialized = True

    def _setup_providers(self, config: TelemetryConfig) -> None:
        """Set up OpenTelemetry providers.

        Args:
            config: Telemetry configuration.
        """
        from opentelemetry import _logs, metrics, trace
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from openjev_tool.telemetry.exporters import (
            create_log_exporter,
            create_metric_exporter,
            create_span_exporter,
        )

        # Create resource with service info
        resource = Resource.create(
            {
                "service.name": config.service_name,
                "service.version": config.service_version,
            }
        )

        # Set up tracing
        span_exporter = create_span_exporter(config)
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        self._tracer_provider = tracer_provider

        # Set up metrics
        metric_exporter = create_metric_exporter(config)
        metric_reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=5000,  # 5 seconds for CLI
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        self._meter_provider = meter_provider

        # Set up logging
        log_exporter = create_log_exporter(config)
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        _logs.set_logger_provider(logger_provider)
        self._logger_provider = logger_provider

        # Attach OTLP handler to Python root logger
        handler = LoggingHandler(
            level=logging.NOTSET,
            logger_provider=logger_provider,
        )
        logging.getLogger().addHandler(handler)

    @property
    def tracer(self) -> Tracer:
        """Get a tracer instance.

        Returns:
            OpenTelemetry Tracer for creating spans.
        """
        from opentelemetry import trace

        if not self._initialized or not getattr(self, "_config", None):
            return trace.get_tracer(__name__)

        return trace.get_tracer(
            self._config.service_name,
            self._config.service_version,
        )

    @property
    def meter(self) -> Meter:
        """Get a meter instance.

        Returns:
            OpenTelemetry Meter for creating metrics.
        """
        from opentelemetry import metrics

        if not self._initialized or not getattr(self, "_config", None):
            return metrics.get_meter(__name__)

        return metrics.get_meter(
            self._config.service_name,
            self._config.service_version,
        )

    @property
    def otel_logger(self) -> OTelLogger:
        """Get an OpenTelemetry logger instance.

        Returns:
            OpenTelemetry Logger for emitting logs via OTLP.
        """
        from opentelemetry import _logs

        if not self._initialized or not getattr(self, "_config", None):
            return _logs.get_logger(__name__)

        return _logs.get_logger(
            self._config.service_name,
            self._config.service_version,
        )

    @property
    def is_enabled(self) -> bool:
        """Check if telemetry is enabled.

        Returns:
            True if telemetry is enabled and initialized.
        """
        return (
            self._initialized
            and getattr(self, "_config", None) is not None
            and self._config.enabled
        )

    def shutdown(self) -> None:
        """Shut down telemetry providers.

        Flushes all pending telemetry data before shutdown.
        Critical for CLI applications to ensure data is exported.
        """
        if not self._initialized:
            return

        if hasattr(self, "_tracer_provider"):
            try:
                self._tracer_provider.force_flush()
                self._tracer_provider.shutdown()
                logger.debug("Tracer provider shut down")
            except Exception as e:
                logger.warning("Error shutting down tracer provider: %s", e)

        if hasattr(self, "_meter_provider"):
            try:
                self._meter_provider.force_flush()
                self._meter_provider.shutdown()
                logger.debug("Meter provider shut down")
            except Exception as e:
                logger.warning("Error shutting down meter provider: %s", e)

        if hasattr(self, "_logger_provider"):
            try:
                self._logger_provider.force_flush()
                self._logger_provider.shutdown()
                logger.debug("Logger provider shut down")
            except Exception as e:
                logger.warning("Error shutting down logger provider: %s", e)

        logger.debug("Telemetry shutdown complete")

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance.

        Primarily for testing purposes.
        """
        if cls._instance is not None:
            cls._instance.shutdown()
        cls._instance = None
        cls._initialized = False
