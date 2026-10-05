"""Config command group: inspect and manage ~/.config/openjev/config.json.

The file is XDG-placed (``OPENJEV_CONFIG`` overrides), atomically written,
and always materialized by ``config init``, ``serve``, ``menubar``, and the
registry so a fresh install has something on disk to inspect and edit.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from openjev_tool.app import make_app
from openjev_tool.config import (
    ConfigError,
    ensure_config,
    load_config,
    save_config,
)
from openjev_tool.output import emit_json, fail

config_app = make_app("Manage ~/.config/openjev/config.json (model registry + server settings).")


@config_app.command(name="path")
def config_path_cmd() -> None:
    """Print the active config file path.

    \b
    Examples:
      openjev-tool config path

      # Where would it be with an override?
      OPENJEV_CONFIG=/tmp/jev.json openjev-tool config path
    """
    from openjev_tool.config import config_path

    typer.echo(str(config_path()))


@config_app.command(name="init")
def config_init() -> None:
    """Create ~/.config/openjev/config.json with defaults if missing (idempotent).

    \b
    Examples:
      # Fresh install: materialize the default registry and server settings
      openjev-tool config init

      # Then inspect it
      openjev-tool config show
    """
    target = ensure_config()
    emit_json({"path": str(target), "created": True})


@config_app.command(name="show")
def config_show() -> None:
    """Print the live config document as JSON.

    \b
    Examples:
      openjev-tool config show

      # Pretty-print with jq
      openjev-tool config show | jq .
    """
    typer.echo(json.dumps(load_config().to_dict(), indent=2))


@config_app.command(name="set")
def config_set(
    host: Annotated[
        str,
        typer.Option("--host", help="Set the server bind address."),
    ] = "",
    port: Annotated[
        int,
        typer.Option("--port", help="Set the server bind port (1..65535)."),
    ] = 0,
) -> None:
    """Update server settings in the config (used as serve defaults).

    \b
    Examples:
      openjev-tool config set --port 9090

      openjev-tool config set --host 0.0.0.0 --port 8080
    """
    if not host and not port:
        fail("nothing to set: pass --host and/or --port")
    config = load_config()
    if host:
        config.server.host = host
    if port:
        config.server.port = port
    try:
        target = save_config(config)
    except ConfigError as exc:
        fail(f"set failed: {exc}")
    emit_json({"path": str(target), "server": config.server.to_dict()})
