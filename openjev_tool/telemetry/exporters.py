"""Exporter factory for OpenTelemetry.

Creates and configures trace, metric, and log exporters based on configuration.
Supports console (development) and OTLP (production) exporters.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openjev_tool.telemetry.config import ExporterType, TelemetryConfig

if TYPE_CHECKING:
    from opentelemetry.sdk._logs.export import LogExporter
    from opentelemetry.sdk.metrics.export import MetricExporter
    from opentelemetry.sdk.trace.export import SpanExporter


def create_span_exporter(config: TelemetryConfig) -> SpanExporter:
    """Create a span exporter based on configuration.

    Args:
        config: Telemetry configuration.

    Returns:
        Configured SpanExporter instance.

    Raises:
        ImportError: If required exporter dependencies are not installed.
    """
    if config.exporter_type == ExporterType.OTLP:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter(
            endpoint=config.otlp_endpoint,
            insecure=config.otlp_insecure,
        )

    # Default to console exporter
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter

    return ConsoleSpanExporter()


def create_metric_exporter(config: TelemetryConfig) -> MetricExporter:
    """Create a metric exporter based on configuration.

    Args:
        config: Telemetry configuration.

    Returns:
        Configured MetricExporter instance.

    Raises:
        ImportError: If required exporter dependencies are not installed.
    """
    if config.exporter_type == ExporterType.OTLP:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        return OTLPMetricExporter(
            endpoint=config.otlp_endpoint,
            insecure=config.otlp_insecure,
        )

    # Default to console exporter
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

    return ConsoleMetricExporter()


def create_log_exporter(config: TelemetryConfig) -> LogExporter:
    """Create a log exporter based on configuration.

    Args:
        config: Telemetry configuration.

    Returns:
        Configured LogExporter instance.

    Raises:
        ImportError: If required exporter dependencies are not installed.
    """
    if config.exporter_type == ExporterType.OTLP:
        from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
            OTLPLogExporter,
        )

        return OTLPLogExporter(  # type: ignore[return-value]
            endpoint=config.otlp_endpoint,
            insecure=config.otlp_insecure,
        )

    # Default to console exporter
    from opentelemetry.sdk._logs.export import ConsoleLogExporter

    return ConsoleLogExporter()  # type: ignore[return-value]
