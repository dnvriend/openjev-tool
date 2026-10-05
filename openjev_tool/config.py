"""Configuration model and persistence for openjev-tool.

The single source of truth is ``~/.config/openjev/config.json`` (XDG paths,
honoring ``OPENJEV_CONFIG`` and ``XDG_CONFIG_HOME`` overrides). It declares the
server bind address, the model registry, the router policy, and stats
persistence. Loads are forgiving about missing keys; anything present but
invalid raises :class:`ConfigError`. Saves are atomic (temp file + replace).
"""

from __future__ import annotations

import json
import os
import string
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_ENV = "OPENJEV_CONFIG"
CONFIG_VERSION = 1
CANONICAL_LABELS: tuple[str, ...] = ("contradiction", "entailment", "neutral")
DEFAULT_CAPABILITIES: tuple[str, ...] = ("score", "noul", "choice", "rerank", "grade")
ALLOWED_CAPABILITIES: frozenset[str] = frozenset(DEFAULT_CAPABILITIES)
DEFAULT_TEMPLATE = "Premise: {premise}\nHypothesis: {hypothesis}"
DEFAULT_ENTRY_ID = "openjev-qwen3.5-4b"
DEFAULT_REPO = "AlexWortega/openjev"
DEFAULT_SUBFOLDER = "qwen3.5-4b-nli"
DEFAULT_REVISION = "f8187e6e11d413d0771bcc7970b85f78e194264c"


class ConfigError(ValueError):
    """Raised when configuration content is invalid."""


def _require_str(data: dict[str, Any], key: str, where: str) -> str:
    """Return ``data[key]`` as a non-empty str or raise ConfigError."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: '{key}' must be a non-empty string, got {value!r}")
    return value


def _opt_str(
    data: dict[str, Any], key: str, default: str, where: str, allow_empty: bool = False
) -> str:
    """Return ``data[key]`` as str when present and valid, else ``default``."""
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigError(f"{where}: '{key}' must be a non-empty string, got {value!r}")
    return value


def _opt_int(data: dict[str, Any], key: str, default: int, where: str) -> int:
    """Return ``data[key]`` as int (bools rejected) when present, else default."""
    if key not in data:
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: '{key}' must be an integer, got {value!r}")
    return value


def _opt_bool(data: dict[str, Any], key: str, default: bool, where: str) -> bool:
    """Return ``data[key]`` as bool when present, else ``default``."""
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: '{key}' must be a boolean, got {value!r}")
    return value


@dataclass(slots=True)
class ModelEntry:
    """One registered judgment model (an NLI cross-encoder checkpoint).

    ``labels`` is the order in which the checkpoint emits its logits; the
    backend permutes them into canonical order for the result builders.
    ``capabilities`` restricts which endpoints the router may send to it.
    """

    id: str
    repo: str
    subfolder: str = ""
    revision: str = "main"
    labels: tuple[str, ...] = CANONICAL_LABELS
    template: str = DEFAULT_TEMPLATE
    capabilities: tuple[str, ...] = DEFAULT_CAPABILITIES
    include_patterns: tuple[str, ...] = ()
    priority: int = 10
    enabled: bool = True

    def supports(self, capability: str) -> bool:
        """Return True when this entry declares ``capability``."""
        return capability in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the config.json model-entry shape."""
        return {
            "id": self.id,
            "repo": self.repo,
            "subfolder": self.subfolder,
            "revision": self.revision,
            "labels": list(self.labels),
            "template": self.template,
            "capabilities": list(self.capabilities),
            "include_patterns": list(self.include_patterns),
            "priority": self.priority,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelEntry:
        """Parse and validate a config.json model entry.

        Args:
            data: The raw entry mapping.

        Returns:
            A validated :class:`ModelEntry`.

        Raises:
            ConfigError: When any field is missing or invalid.
        """
        where = "model entry"
        model_id = _require_str(data, "id", where)
        repo = _require_str(data, "repo", where)
        subfolder = _opt_str(data, "subfolder", "", where, allow_empty=True)
        revision = _opt_str(data, "revision", "main", where)
        template = _opt_str(data, "template", DEFAULT_TEMPLATE, where)
        priority = _opt_int(data, "priority", 10, where)
        enabled = _opt_bool(data, "enabled", True, where)
        raw_labels = data.get("labels", list(CANONICAL_LABELS))
        if not isinstance(raw_labels, list) or not all(isinstance(v, str) for v in raw_labels):
            raise ConfigError(f"{model_id}: 'labels' must be a list of strings")
        labels = tuple(raw_labels)
        if len(labels) != 3 or set(labels) != set(CANONICAL_LABELS):
            raise ConfigError(
                f"{model_id}: 'labels' must be a permutation of {list(CANONICAL_LABELS)}, "
                f"got {list(labels)}"
            )
        raw_caps = data.get("capabilities", list(DEFAULT_CAPABILITIES))
        if not isinstance(raw_caps, list) or not all(isinstance(v, str) for v in raw_caps):
            raise ConfigError(f"{model_id}: 'capabilities' must be a list of strings")
        caps = tuple(dict.fromkeys(raw_caps))
        if not caps:
            raise ConfigError(f"{model_id}: 'capabilities' must not be empty")
        unknown = [c for c in caps if c not in ALLOWED_CAPABILITIES]
        if unknown:
            raise ConfigError(
                f"{model_id}: unknown capabilities {unknown}, "
                f"allowed: {sorted(ALLOWED_CAPABILITIES)}"
            )
        raw_includes = data.get("include_patterns", [])
        if not isinstance(raw_includes, list) or not all(
            isinstance(v, str) and v.strip() for v in raw_includes
        ):
            raise ConfigError(f"{model_id}: 'include_patterns' must be a list of glob strings")
        includes = tuple(raw_includes)
        try:
            validate_template_placeholders(template)
        except ConfigError as exc:
            raise ConfigError(f"{model_id}: 'template': {exc}") from exc
        return cls(
            id=model_id,
            repo=repo,
            subfolder=subfolder,
            revision=revision,
            labels=labels,
            template=template,
            capabilities=caps,
            include_patterns=includes,
            priority=priority,
            enabled=enabled,
        )


@dataclass(slots=True)
class ServerConfig:
    """Bind address for the local judgment server."""

    host: str = "127.0.0.1"
    port: int = 8080

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the config.json server shape."""
        return {"host": self.host, "port": self.port}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerConfig:
        """Parse and validate the server section."""
        host = _opt_str(data, "host", "127.0.0.1", "server")
        port = _opt_int(data, "port", 8080, "server")
        if not 1 <= port <= 65535:
            raise ConfigError(f"server: 'port' must be in 1..65535, got {port}")
        return cls(host=host, port=port)


@dataclass(slots=True)
class RouterConfig:
    """Policy for picking the model when several are enabled."""

    policy: str = "capability-priority-stats"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the config.json router shape."""
        return {"policy": self.policy}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouterConfig:
        """Parse and validate the router section."""
        return cls(policy=_opt_str(data, "policy", "capability-priority-stats", "router"))


@dataclass(slots=True)
class StatsConfig:
    """Stats persistence toggle."""

    persist: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the config.json stats shape."""
        return {"persist": self.persist}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatsConfig:
        """Parse and validate the stats section."""
        return cls(persist=_opt_bool(data, "persist", True, "stats"))


@dataclass(slots=True)
class Config:
    """Root configuration document stored at the config path."""

    version: int = CONFIG_VERSION
    server: ServerConfig = field(default_factory=ServerConfig)
    models: list[ModelEntry] = field(default_factory=lambda: default_models())
    router: RouterConfig = field(default_factory=RouterConfig)
    stats: StatsConfig = field(default_factory=StatsConfig)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the whole document for config.json."""
        return {
            "version": self.version,
            "server": self.server.to_dict(),
            "router": self.router.to_dict(),
            "stats": self.stats.to_dict(),
            "models": [entry.to_dict() for entry in self.models],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Config:
        """Parse and validate a full config document.

        Missing sections fall back to defaults; present-but-invalid content
        raises :class:`ConfigError`.
        """
        version = data.get("version", CONFIG_VERSION)
        if version != CONFIG_VERSION:
            raise ConfigError(f"unsupported config version {version!r}, expected {CONFIG_VERSION}")
        raw_models = data.get("models", [])
        if not isinstance(raw_models, list):
            raise ConfigError("'models' must be a list")
        return cls(
            version=CONFIG_VERSION,
            server=ServerConfig.from_dict(data.get("server", {})),
            models=[ModelEntry.from_dict(entry) for entry in raw_models],
            router=RouterConfig.from_dict(data.get("router", {})),
            stats=StatsConfig.from_dict(data.get("stats", {})),
        )


def default_models() -> list[ModelEntry]:
    """The registry defaults: the pinned openjev checkpoint plus alternatives."""
    return [
        ModelEntry(
            id=DEFAULT_ENTRY_ID,
            repo=DEFAULT_REPO,
            subfolder=DEFAULT_SUBFOLDER,
            revision=DEFAULT_REVISION,
            priority=10,
            enabled=True,
        ),
        ModelEntry(
            id="nli-deberta-v3-small",
            repo="cross-encoder/nli-deberta-v3-small",
            revision="main",
            include_patterns=(
                "*.safetensors",
                "*.bin",
                "*.model",
                "*.json",
                "*.txt",
                "*.md",
                "LICENSE*",
            ),
            priority=3,
            enabled=False,
        ),
        ModelEntry(
            id="roberta-large-mnli",
            repo="FacebookAI/roberta-large-mnli",
            revision="main",
            labels=("contradiction", "neutral", "entailment"),
            capabilities=("noul", "score"),
            priority=5,
            enabled=False,
        ),
    ]


def default_config() -> Config:
    """A fresh config with the default model registry."""
    return Config()


def config_path() -> Path:
    """Return the config file path (OPENJEV_CONFIG > XDG_CONFIG_HOME > ~/.config)."""
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / "openjev" / "config.json"


def load_config(path: Path | None = None) -> Config:
    """Load the config from ``path`` (default: :func:`config_path`).

    A missing file yields the default config without writing anything.

    Raises:
        ConfigError: When the file exists but is not valid JSON or fails validation.
    """
    target = path if path is not None else config_path()
    if not target.exists():
        return default_config()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {target}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"config root must be a JSON object: {target}")
    return Config.from_dict(data)


def ensure_config(path: Path | None = None) -> Path:
    """Materialize the config on disk: create the directory and write the
    default document when the file does not exist yet (idempotent).

    Args:
        path: Target path; defaults to :func:`config_path`.

    Returns:
        The config path, now guaranteed to exist on disk.
    """
    target = path if path is not None else config_path()
    if not target.exists():
        save_config(default_config(), target)
    return target


def save_config(config: Config, path: Path | None = None) -> Path:
    """Atomically write ``config`` to ``path`` (default: :func:`config_path`).

    Writes a temp file beside the target and replaces it, so readers never
    observe a partial document. Returns the path written.
    """
    target = path if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f"{target.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    return target


def validate_template_placeholders(template: str) -> None:
    """Raise ConfigError unless ``template`` formats with premise/hypothesis only."""
    fields = {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name is not None
    }
    unknown = fields - {"premise", "hypothesis"}
    if unknown or "premise" not in fields or "hypothesis" not in fields:
        raise ConfigError(
            f"template must use exactly {{premise}} and {{hypothesis}}, got fields {sorted(fields)}"
        )
