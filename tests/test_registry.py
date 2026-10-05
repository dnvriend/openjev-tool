"""Tests for registry pure operations and persistence round trip."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjev_tool.config import Config, ModelEntry
from openjev_tool.registry import (
    Registry,
    RegistryError,
    add_entry,
    delete_entry_cache,
    entry_patterns,
    remove_entry,
    set_entry_enabled,
    snapshot_patterns,
)


def _entry(model_id: str = "extra") -> ModelEntry:
    return ModelEntry(id=model_id, repo=f"org/{model_id}", capabilities=("noul",))


def test_add_entry_rejects_duplicate_id() -> None:
    config = Config()
    add_entry(config, _entry())
    with pytest.raises(RegistryError, match="already registered"):
        add_entry(config, _entry())


def test_remove_entry_unknown_raises() -> None:
    with pytest.raises(RegistryError, match="unknown model id"):
        remove_entry(Config(), "nope")


def test_remove_entry_mutates_list() -> None:
    config = Config()
    add_entry(config, _entry())
    remove_entry(config, "extra")
    assert [e.id for e in config.models if e.id == "extra"] == []


def test_set_entry_enabled_flips_flag() -> None:
    config = Config()
    target = config.models[0].id
    set_entry_enabled(config, target, False)
    assert config.models[0].enabled is False
    with pytest.raises(RegistryError):
        set_entry_enabled(config, "nope", True)


def test_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    registry = Registry.load(path)
    registry.add(_entry("second"))
    registry.set_enabled(registry.config.models[0].id, False)

    reloaded = Registry.load(path)
    ids = [e.id for e in reloaded.config.models]
    assert "second" in ids
    assert reloaded.config.models[0].enabled is False

    reloaded.remove("second")
    final = Registry.load(path)
    assert "second" not in [e.id for e in final.config.models]


def test_registry_entry_lookup(tmp_path: Path) -> None:
    registry = Registry.load(tmp_path / "config.json")
    first = registry.config.models[0]
    assert registry.entry(first.id) == first
    with pytest.raises(RegistryError, match="unknown model id"):
        registry.entry("missing")


def test_snapshot_patterns() -> None:
    assert snapshot_patterns("qwen3.5-4b-nli") == ["qwen3.5-4b-nli/*", "*.md", "LICENSE*"]
    assert snapshot_patterns("") is None


def test_entry_patterns_include_patterns_win() -> None:
    explicit = ModelEntry(id="m", repo="org/m", include_patterns=("*.safetensors", "*.json"))
    assert entry_patterns(explicit) == ["*.safetensors", "*.json"]
    subfolder = ModelEntry(id="s", repo="org/s", subfolder="weights")
    assert entry_patterns(subfolder) == ["weights/*", "*.md", "LICENSE*"]
    everything = ModelEntry(id="e", repo="org/e")
    assert entry_patterns(everything) is None


def test_delete_entry_cache_removes_repo_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub.constants as hf_constants

    cache_root = tmp_path / "hub"
    repo_dir = cache_root / "models--org--small"
    (repo_dir / "blobs").mkdir(parents=True)
    (repo_dir / "blobs" / "weight.bin").write_bytes(b"0" * 4096)
    (repo_dir / "refs").mkdir()
    monkeypatch.setattr(hf_constants, "HF_HUB_CACHE", str(cache_root))

    entry = ModelEntry(id="small", repo="org/small")
    assert delete_entry_cache(entry) == 4096
    assert not repo_dir.exists()
    assert delete_entry_cache(entry) == 0


def test_delete_entry_cache_rejects_traversal() -> None:
    entry = ModelEntry(id="evil", repo="../../etc/passwd")
    assert delete_entry_cache(entry) == 0
