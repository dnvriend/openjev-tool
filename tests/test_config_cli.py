"""Tests for the config CLI group (path | init | show | set)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openjev_tool.config import load_config
from openjev_tool.config_cli import config_app

runner = CliRunner()


@pytest.fixture
def config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the CLI against a temp config path (initially missing)."""
    path = tmp_path / "config.json"
    monkeypatch.setenv("OPENJEV_CONFIG", str(path))
    return path


def test_config_group_prints_help_without_subcommand() -> None:
    result = runner.invoke(config_app, [])
    assert result.exit_code == 0
    assert "Manage" in result.output


def test_config_path_prints_active_path(config_env: Path) -> None:
    result = runner.invoke(config_app, ["path"])
    assert result.exit_code == 0
    assert str(config_env) in result.output


def test_config_init_creates_file_once(config_env: Path) -> None:
    assert not config_env.exists()
    first = runner.invoke(config_app, ["init"])
    assert first.exit_code == 0, first.output
    assert config_env.exists()
    payload = json.loads(first.output.strip().splitlines()[-1])
    assert payload["created"] is True

    second = runner.invoke(config_app, ["init"])
    assert second.exit_code == 0
    assert json.loads(second.output.strip().splitlines()[-1])["path"] == str(config_env)


def test_config_show_prints_document(config_env: Path) -> None:
    runner.invoke(config_app, ["init"])
    result = runner.invoke(config_app, ["show"])
    assert result.exit_code == 0
    document = json.loads(result.output)
    assert document["version"] == 1
    assert any(model["id"] == "openjev-qwen3.5-4b" for model in document["models"])


def test_config_set_updates_server_section(config_env: Path) -> None:
    runner.invoke(config_app, ["init"])
    result = runner.invoke(config_app, ["set", "--port", "9090"])
    assert result.exit_code == 0, result.output
    assert load_config(config_env).server.port == 9090


def test_config_set_requires_an_option(config_env: Path) -> None:
    result = runner.invoke(config_app, ["set"])
    assert result.exit_code == 1
    assert "nothing to set" in result.output
