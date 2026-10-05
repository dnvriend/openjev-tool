"""CLI entry point for openjev-tool.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

import atexit
from typing import Annotated

import typer

from openjev_tool.completion import completion_app
from openjev_tool.config import load_config
from openjev_tool.config_cli import config_app
from openjev_tool.jev import jev_app
from openjev_tool.logging_config import setup_logging
from openjev_tool.models import models_app
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
    """A local judgment-model server for Jev-style agentic engineering

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


@app.command()
def serve(
    host: Annotated[
        str, typer.Option("--host", help="Bind address (default: config server.host).")
    ] = "",
    port: Annotated[
        int, typer.Option("--port", help="Bind port (default: config server.port).")
    ] = 0,
    device: Annotated[
        str,
        typer.Option("--device", envvar="OPENJEV_DEVICE", help="Torch device (mps, cuda, cpu)."),
    ] = "",
    fake: Annotated[
        bool,
        typer.Option("--fake", help="Serve the deterministic FakeBackend (no model, no torch)."),
    ] = False,
    supervised: Annotated[
        bool,
        typer.Option("--supervised", help="Write a pidfile for the menu bar supervisor."),
    ] = False,
) -> None:
    """Start the local judgment server (host/port default to config.json).

    \b
    Examples:
      # Start on the configured host/port (~/.config/openjev/config.json)
      openjev-tool serve

      # Fake backend for instant, model-free development
      openjev-tool serve --fake --port 8080

      # Supervised by the menu bar app (writes a pidfile)
      openjev-tool serve --supervised

      # Check health, stats, models
      curl -s http://127.0.0.1:8080/health
      curl -s http://127.0.0.1:8080/stats
      curl -s http://127.0.0.1:8080/v1/models

      # Score something
      curl -s -X POST http://127.0.0.1:8080/v1/score \\
        -H 'Content-Type: application/json' \\
        -d '{"premise": "A man plays guitar.", "hypothesis": "Someone makes music."}'
    """
    from openjev_tool.server import run_server

    config = load_config()
    bind_host = host or config.server.host
    bind_port = port or config.server.port
    typer.secho(
        f"starting openjev server on http://{bind_host}:{bind_port}",
        err=True,
        fg=typer.colors.GREEN,
    )
    run_server(
        host=host or None,
        port=port or None,
        device=device,
        fake=fake,
        supervised=supervised,
    )


@app.command()
def menubar(
    fake: Annotated[
        bool,
        typer.Option("--fake", help="Start the supervised server with the FakeBackend."),
    ] = False,
) -> None:
    """Run the macOS menu bar controller (toolbar icon) for the openjev server.

    \b
    Examples:
      # Start the menu bar app (needs: uv sync --extra menubar)
      openjev-tool menubar

      # Menu bar + fake server (no model download, instant start)
      openjev-tool menubar --fake

    The icon shows ○ stopped / ◐ starting / ● running / ✕ error. Start/Stop
    supervises `openjev-tool serve --supervised`; model checkboxes write
    ~/.config/openjev/config.json and hot-reload a running server; the stats
    line updates live; 'Open Playground' opens http://<host>:<port>/ui/.
    """
    from openjev_tool.menubar import run_menubar

    run_menubar(fake=fake)


app.add_typer(completion_app, name="completion")
app.add_typer(jev_app, name="jev")
app.add_typer(models_app, name="models")
app.add_typer(config_app, name="config")


if __name__ == "__main__":
    app()
