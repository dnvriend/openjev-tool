"""Shared Typer app factory.

Every command group prints its help (instead of erroring with "Missing
command") when invoked without a subcommand. This is an AI-agent-first
convention: running ``openjev-tool <group>`` returns usable
guidance instead of a bare error. Use :func:`make_app` for any group that
has subcommands.
"""

from __future__ import annotations

import typer


def make_app(help_text: str) -> typer.Typer:
    """Create a Typer group that shows help when no subcommand is given.

    Args:
        help_text: Short description shown in the parent app's command list
            and at the top of this group's help.

    Returns:
        A configured :class:`typer.Typer` whose callback prints help and exits
        when no subcommand is invoked.
    """
    app = typer.Typer(help=help_text, invoke_without_command=True)

    @app.callback(invoke_without_command=True)
    def _show_help_when_no_subcommand(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            typer.echo(ctx.get_help())
            raise typer.Exit()

    return app
