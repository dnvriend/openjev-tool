"""Telemetry configuration.

Loads configuration from environment variables following OpenTelemetry conventions.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum

from openjev_tool import __version__


class ExporterType(StrEnum):
    """Supported telemetry exporters."""

    CONSOLE = "console"
    OTLP = "otlp"


@dataclass
class TelemetryConfig:
    """Configuration for OpenTelemetry.

    Attributes:
        enabled: Whether telemetry is enabled.
        service_name: Name of the service in traces/metrics.
        service_version: Version of the service.
        exporter_type: Type of exporter to use (console or otlp).
        otlp_endpoint: OTLP collector endpoint (for Grafana Alloy, OTEL Collector).
        otlp_insecure: Whether to use insecure connection for OTLP.
    """

    enabled: bool = False
    service_name: str = "openjev-tool"
    service_version: str = field(default_factory=lambda: __version__)
    exporter_type: ExporterType = ExporterType.CONSOLE
    otlp_endpoint: str = "http://localhost:4317"
    otlp_insecure: bool = True

    @classmethod
    def from_env(cls) -> TelemetryConfig:
        """Create configuration from environment variables.

        Environment Variables:
            OTEL_ENABLED: Enable telemetry (default: false)
            OTEL_SERVICE_NAME: Service name (default: openjev-tool)
            OTEL_EXPORTER_TYPE: Exporter type - console or otlp (default: console)
            OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint (default: http://localhost:4317)
            OTEL_EXPORTER_OTLP_INSECURE: Use insecure connection (default: true)

        Returns:
            TelemetryConfig instance populated from environment.
        """
        enabled_str = os.environ.get("OTEL_ENABLED", "false").lower()
        enabled = enabled_str in ("true", "1", "yes")

        exporter_str = os.environ.get("OTEL_EXPORTER_TYPE", "console").lower()
        try:
            exporter_type = ExporterType(exporter_str)
        except ValueError:
            exporter_type = ExporterType.CONSOLE

        insecure_str = os.environ.get("OTEL_EXPORTER_OTLP_INSECURE", "true").lower()
        otlp_insecure = insecure_str in ("true", "1", "yes")

        return cls(
            enabled=enabled,
            service_name=os.environ.get("OTEL_SERVICE_NAME", "openjev-tool"),
            exporter_type=exporter_type,
            otlp_endpoint=os.environ.get(
                "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
            ),
            otlp_insecure=otlp_insecure,
        )
