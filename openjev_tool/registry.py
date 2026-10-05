"""Model registry: resolve, mutate, download, and inspect config entries.

Pure list operations (:func:`add_entry`, :func:`remove_entry`,
:func:`set_entry_enabled`) mutate a :class:`~openjev_tool.config.Config` in
memory; :class:`Registry` wraps them with load/save to the config file.
Downloads go through ``huggingface_hub.snapshot_download`` and never
re-download cached revisions.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openjev_tool.config import Config, ModelEntry, config_path, load_config, save_config


class RegistryError(ValueError):
    """Raised when a registry lookup or mutation cannot be performed."""


def add_entry(config: Config, entry: ModelEntry) -> None:
    """Append ``entry``; reject duplicate ids."""
    if any(existing.id == entry.id for existing in config.models):
        raise RegistryError(f"model id already registered: {entry.id}")
    config.models.append(entry)


def remove_entry(config: Config, model_id: str) -> None:
    """Remove the entry with ``model_id``; raise RegistryError when missing."""
    remaining = [entry for entry in config.models if entry.id != model_id]
    if len(remaining) == len(config.models):
        raise RegistryError(f"unknown model id: {model_id}")
    config.models[:] = remaining


def set_entry_enabled(config: Config, model_id: str, enabled: bool) -> None:
    """Flip the enabled flag of ``model_id``; raise RegistryError when missing."""
    for entry in config.models:
        if entry.id == model_id:
            entry.enabled = enabled
            return
    raise RegistryError(f"unknown model id: {model_id}")


def snapshot_patterns(subfolder: str) -> list[str] | None:
    """HF allow-patterns for a subfolder checkpoint; None downloads everything."""
    if subfolder:
        return [f"{subfolder}/*", "*.md", "LICENSE*"]
    return None


def entry_patterns(entry: ModelEntry) -> list[str] | None:
    """allow-patterns for an entry: explicit includes win over subfolder defaults."""
    if entry.include_patterns:
        return list(entry.include_patterns)
    return snapshot_patterns(entry.subfolder)


def download_entry(entry: ModelEntry) -> str:
    """Download the entry's checkpoint into the HF cache; return the local path.

    Raises:
        Exception: Whatever huggingface_hub raises on network/auth failure.
    """
    from huggingface_hub import snapshot_download

    return str(
        snapshot_download(
            repo_id=entry.repo,
            allow_patterns=entry_patterns(entry),
            revision=entry.revision,
        )
    )


def entry_downloaded(entry: ModelEntry) -> bool:
    """True when the entry's files are already in the local HF cache (offline check)."""
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=entry.repo,
            allow_patterns=entry_patterns(entry),
            revision=entry.revision,
            local_files_only=True,
        )
        return True
    except Exception:
        return False


def delete_entry_cache(entry: ModelEntry) -> int:
    """Delete the entry's repo from the local HF cache; return freed bytes.

    The registry entry itself is untouched. Returns 0 when the repo is not
    cached or the repo id is unsafe (path traversal).
    """
    if ".." in entry.repo:
        return 0
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        repo_dir = Path(HF_HUB_CACHE) / ("models--" + entry.repo.replace("/", "--"))
        if not repo_dir.exists():
            return 0
        blobs = repo_dir / "blobs"
        freed = sum(path.stat().st_size for path in blobs.glob("*") if path.is_file())
        shutil.rmtree(repo_dir, ignore_errors=True)
        return freed
    except Exception:
        return 0


@dataclass(slots=True)
class Registry:
    """A config plus its on-disk path, with save-through mutations."""

    config: Config = field(default_factory=Config)
    path: Path = field(default_factory=config_path)

    @classmethod
    def load(cls, path: Path | None = None) -> Registry:
        """Load the registry from ``path`` (default: the openjev config path).

        Materializes the default config file on disk when it does not exist
        yet, so the registry is always inspectable at its config path.
        """
        from openjev_tool.config import ensure_config

        target = ensure_config(path if path is not None else config_path())
        return cls(config=load_config(target), path=target)

    def save(self) -> Path:
        """Persist the current config atomically."""
        return save_config(self.config, self.path)

    def entry(self, model_id: str) -> ModelEntry:
        """Return the entry with ``model_id``.

        Raises:
            RegistryError: When no entry has that id.
        """
        for entry in self.config.models:
            if entry.id == model_id:
                return entry
        raise RegistryError(f"unknown model id: {model_id}")

    def add(self, entry: ModelEntry) -> None:
        """Add and persist ``entry``."""
        add_entry(self.config, entry)
        self.save()

    def remove(self, model_id: str) -> None:
        """Remove and persist the entry with ``model_id``."""
        remove_entry(self.config, model_id)
        self.save()

    def set_enabled(self, model_id: str, enabled: bool) -> None:
        """Flip and persist the enabled flag of ``model_id``."""
        set_entry_enabled(self.config, model_id, enabled)
        self.save()

    def download(self, model_id: str) -> dict[str, Any]:
        """Download the entry with ``model_id``; return the JSON result shape."""
        entry = self.entry(model_id)
        path = download_entry(entry)
        return {"id": entry.id, "repo": entry.repo, "subfolder": entry.subfolder, "path": path}

    def is_downloaded(self, model_id: str) -> bool:
        """Offline check whether ``model_id`` is present in the HF cache."""
        return entry_downloaded(self.entry(model_id))
