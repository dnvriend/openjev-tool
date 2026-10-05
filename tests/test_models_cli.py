"""Tests for the models CLI group (CliRunner + temp config via OPENJEV_CONFIG)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openjev_tool.config import load_config
from openjev_tool.models import format_models_table, models_app

runner = CliRunner()


@pytest.fixture
def config_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the CLI against a temp config file."""
    path = tmp_path / "config.json"
    monkeypatch.setenv("OPENJEV_CONFIG", str(path))
    return path


def test_models_group_prints_help_without_subcommand() -> None:
    result = runner.invoke(models_app, [])
    assert result.exit_code == 0
    assert "Manage the judgment-model registry" in result.output


def test_add_then_list_round_trip(config_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("openjev_tool.models.entry_downloaded", lambda entry: True)
    result = runner.invoke(
        models_app,
        [
            "add",
            "FacebookAI/xnli-mnli",
            "--labels",
            "contradiction,neutral,entailment",
            "--capabilities",
            "noul,score",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["id"] == "xnli-mnli"

    listing = runner.invoke(models_app, ["list"])
    assert listing.exit_code == 0
    assert "xnli-mnli" in listing.output
    assert "yes" in listing.output

    config = load_config(config_env)
    added = [e for e in config.models if e.id == "xnli-mnli"]
    assert added and added[0].labels == ("contradiction", "neutral", "entailment")


def test_add_duplicate_fails(config_env: Path) -> None:
    args = ["add", "org/model-a"]
    assert runner.invoke(models_app, args).exit_code == 0
    result = runner.invoke(models_app, args)
    assert result.exit_code == 1
    assert "already registered" in result.output


def test_enable_disable_persist(config_env: Path) -> None:
    assert runner.invoke(models_app, ["add", "org/model-b", "--disabled"]).exit_code == 0
    assert runner.invoke(models_app, ["enable", "model-b"]).exit_code == 0
    assert load_config(config_env).models[-1].enabled is True

    assert runner.invoke(models_app, ["disable", "model-b"]).exit_code == 0
    assert load_config(config_env).models[-1].enabled is False


def test_disable_unknown_model_fails(config_env: Path) -> None:
    result = runner.invoke(models_app, ["disable", "ghost"])
    assert result.exit_code == 1
    assert "unknown model id" in result.output


def test_remove_persists(config_env: Path) -> None:
    assert runner.invoke(models_app, ["add", "org/doomed"]).exit_code == 0
    result = runner.invoke(models_app, ["remove", "doomed"])
    assert result.exit_code == 0
    assert json.loads(result.output.strip().splitlines()[-1]) == {"removed": "doomed"}
    assert "doomed" not in [e.id for e in load_config(config_env).models]


def test_remove_unknown_fails(config_env: Path) -> None:
    result = runner.invoke(models_app, ["remove", "ghost"])
    assert result.exit_code == 1


def test_purge_reports_freed_bytes(config_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(models_app, ["add", "org/model-c"])
    monkeypatch.setattr("openjev_tool.models.delete_entry_cache", lambda entry: 4096)
    result = runner.invoke(models_app, ["purge", "model-c"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output.strip().splitlines()[-1]) == {
        "id": "model-c",
        "repo": "org/model-c",
        "freed_bytes": 4096,
    }


def test_purge_unknown_fails(config_env: Path) -> None:
    result = runner.invoke(models_app, ["purge", "ghost"])
    assert result.exit_code == 1
    assert "unknown model id" in result.output


def test_add_rejects_bad_labels(config_env: Path) -> None:
    result = runner.invoke(models_app, ["add", "org/bad", "--labels", "a,b,c"])
    assert result.exit_code == 1
    assert "labels" in result.output


def test_format_models_table_columns() -> None:
    config = load_config()
    table = format_models_table(config, {config.models[0].id: True})
    lines = table.splitlines()
    assert lines[0].startswith("ID")
    assert "openjev-qwen3.5-4b" in lines[1]
    assert "yes" in lines[1]
