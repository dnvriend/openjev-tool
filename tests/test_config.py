"""Tests for config parsing, validation, and persistence (pure, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjev_tool.config import (
    CANONICAL_LABELS,
    CONFIG_VERSION,
    DEFAULT_ENTRY_ID,
    DEFAULT_REPO,
    Config,
    ConfigError,
    ModelEntry,
    config_path,
    default_config,
    ensure_config,
    load_config,
    save_config,
)


def test_default_config_has_enabled_default_entry() -> None:
    config = default_config()
    entry = next(e for e in config.models if e.id == DEFAULT_ENTRY_ID)
    assert entry.repo == DEFAULT_REPO
    assert entry.enabled is True
    assert entry.labels == CANONICAL_LABELS
    assert entry.supports("score") and entry.supports("rerank")


def test_default_config_includes_small_model() -> None:
    config = default_config()
    small = next(e for e in config.models if e.id == "nli-deberta-v3-small")
    assert small.repo == "cross-encoder/nli-deberta-v3-small"
    assert small.enabled is False


def test_ensure_config_creates_dirs_and_file_idempotently(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "openjev" / "config.json"
    first = ensure_config(target)
    assert first == target
    assert target.exists()
    written_at = target.stat().st_mtime_ns
    again = ensure_config(target)
    assert again == target
    assert target.stat().st_mtime_ns == written_at
    assert load_config(target).version == CONFIG_VERSION


def test_config_path_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENJEV_CONFIG", "/tmp/custom/openjev.json")
    assert config_path() == Path("/tmp/custom/openjev.json")
    monkeypatch.delenv("OPENJEV_CONFIG")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == Path.home() / ".config" / "openjev" / "config.json"
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg")
    assert config_path() == Path("/tmp/xdg/openjev/config.json")


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    config = default_config()
    config.server.port = 9099
    target = save_config(config, tmp_path / "config.json")
    assert target.exists()
    loaded = load_config(target)
    assert loaded.server.port == 9099
    assert [e.id for e in loaded.models] == [e.id for e in config.models]


def test_load_missing_file_returns_default(tmp_path: Path) -> None:
    assert load_config(tmp_path / "missing.json").version == CONFIG_VERSION


def test_load_invalid_json_raises(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(target)


def test_load_non_object_root_raises(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError, match="object"):
        load_config(target)


def test_unsupported_version_raises() -> None:
    with pytest.raises(ConfigError, match="version"):
        Config.from_dict({"version": 99})


def test_model_entry_label_permutation_accepted() -> None:
    entry = ModelEntry.from_dict(
        {"id": "m", "repo": "org/m", "labels": ["contradiction", "neutral", "entailment"]}
    )
    assert entry.labels == ("contradiction", "neutral", "entailment")


@pytest.mark.parametrize(
    ("labels", "why"),
    [
        (["contradiction", "entailment"], "too few"),
        (["contradiction", "entailment", "entailment"], "duplicate"),
        (["contradiction", "entailment", "banana"], "unknown label"),
        ("entailment", "not a list"),
    ],
)
def test_model_entry_bad_labels_rejected(labels: object, why: str) -> None:
    with pytest.raises(ConfigError, match="labels"):
        ModelEntry.from_dict({"id": "m", "repo": "org/m", "labels": labels})


def test_model_entry_unknown_capability_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown capabilities"):
        ModelEntry.from_dict({"id": "m", "repo": "org/m", "capabilities": ["score", "chat"]})


def test_model_entry_empty_capabilities_rejected() -> None:
    with pytest.raises(ConfigError, match="not be empty"):
        ModelEntry.from_dict({"id": "m", "repo": "org/m", "capabilities": []})


def test_model_entry_missing_id_or_repo_rejected() -> None:
    with pytest.raises(ConfigError, match="id"):
        ModelEntry.from_dict({"repo": "org/m"})
    with pytest.raises(ConfigError, match="repo"):
        ModelEntry.from_dict({"id": "m"})


def test_model_entry_template_validation() -> None:
    with pytest.raises(ConfigError, match="template"):
        ModelEntry.from_dict({"id": "m", "repo": "org/m", "template": "no placeholders"})
    with pytest.raises(ConfigError, match="template"):
        ModelEntry.from_dict({"id": "m", "repo": "org/m", "template": "{premise} only"})
    with pytest.raises(ConfigError, match="template"):
        ModelEntry.from_dict(
            {"id": "m", "repo": "org/m", "template": "{premise} {extra} {hypothesis}"}
        )


def test_server_port_bounds() -> None:
    with pytest.raises(ConfigError, match="port"):
        Config.from_dict({"server": {"port": 0}})
    with pytest.raises(ConfigError, match="port"):
        Config.from_dict({"server": {"port": 70000}})


def test_bool_rejected_for_int_fields() -> None:
    with pytest.raises(ConfigError, match="priority"):
        ModelEntry.from_dict({"id": "m", "repo": "org/m", "priority": True})


def test_save_is_atomic_and_clean(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    save_config(default_config(), target)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "config.json"]
    assert leftovers == []
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["version"] == CONFIG_VERSION
    assert data["server"]["host"] == "127.0.0.1"
