"""CLI entry point for openjev-tool.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

import atexit
from typing import Annotated

import typer

from openjev_tool.completion import completion_app
from openjev_tool.logging_config import setup_logging
from openjev_tool.telemetry import TelemetryConfig, TelemetryService

app = typer.Typer(invoke_without_command=True)


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo("openjev-tool version 0.1.0")
        raise typer.Exit()


def _shutdown_telemetry() -> None:
    """Shutdown telemetry on exit."""
    TelemetryService.get_instance().shutdown()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            count=True,
            help="Enable verbose output (use -v for INFO, -vv for DEBUG, -vvv for TRACE)",
        ),
    ] = 0,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = None,
    telemetry: Annotated[
        bool,
        typer.Option(
            "--telemetry",
            envvar="OTEL_ENABLED",
            help="Enable OpenTelemetry observability.",
        ),
    ] = False,
) -> None:
    """A Python CLI tool

    Run a subcommand. With no subcommand, prints this help and exits.
    """
    setup_logging(verbose)

    config = TelemetryConfig.from_env()
    config.enabled = telemetry or config.enabled
    TelemetryService.get_instance().initialize(config)
    atexit.register(_shutdown_telemetry)

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


app.add_typer(completion_app, name="completion")


if __name__ == "__main__":
    app()
