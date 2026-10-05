"""Shared stdout/stderr helpers for CLI commands.

Status/data goes to stdout as a single JSON line; diagnostics go to stderr in
red and exit non-zero.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

import typer


def emit_json(payload: dict[str, Any]) -> None:
    """Print ``payload`` as a single JSON line to stdout."""
    typer.echo(json.dumps(payload))


def fail(message: str) -> NoReturn:
    """Print ``message`` to stderr in red and exit with code 1."""
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)
