"""Tests for the shared Typer app factory (make_app)."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from openjev_tool.app import make_app


def test_make_app_prints_help_on_no_subcommand() -> None:
    app = make_app("Manage things.")

    @app.command()
    def foo() -> None:
        """A foo command."""

    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0
    assert "Manage things." in result.output


def test_make_app_runs_subcommand() -> None:
    app = make_app("Manage things.")
    called: list[int] = []

    @app.command()
    def foo() -> None:
        """A foo command."""
        called.append(1)
        typer.echo("ran-foo")

    result = CliRunner().invoke(app, ["foo"])
    assert result.exit_code == 0
    assert "ran-foo" in result.output
    assert called == [1]
