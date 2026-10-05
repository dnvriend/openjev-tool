"""Models command group: manage the local judgment-model registry.

All state lives in ``~/.config/openjev/config.json`` (override with
``OPENJEV_CONFIG``). Mutations persist immediately; status/data goes to
stdout as JSON, diagnostics to stderr.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated

import typer

from openjev_tool.app import make_app
from openjev_tool.config import (
    CANONICAL_LABELS,
    DEFAULT_CAPABILITIES,
    DEFAULT_TEMPLATE,
    Config,
    ConfigError,
    ModelEntry,
)
from openjev_tool.output import emit_json, fail
from openjev_tool.registry import (
    Registry,
    RegistryError,
    delete_entry_cache,
    entry_downloaded,
)

models_app = make_app("Manage the judgment-model registry (~/.config/openjev/config.json).")


def format_models_table(config: Config, downloaded: Mapping[str, bool]) -> str:
    """Render the registry as a fixed-width table (pure; used by ``models list``).

    Args:
        config: The config whose ``models`` list is rendered.
        downloaded: Mapping of model id to cached-in-HF-cache flag.

    Returns:
        The multi-line table string (no trailing newline).
    """
    lines = [f"{'ID':<28} {'ON':<3} {'PRI':>3} {'CACHE':<5} {'REPO':<34} CAPABILITIES"]
    for entry in config.models:
        on = "yes" if entry.enabled else "no"
        cached = "yes" if downloaded.get(entry.id, False) else "no"
        lines.append(
            f"{entry.id:<28} {on:<3} {entry.priority:>3} {cached:<5} "
            f"{entry.repo:<34} {','.join(entry.capabilities)}"
        )
    return "\n".join(lines)


def _parse_csv(value: str, field: str) -> tuple[str, ...]:
    """Split a comma-separated option value; reject empty items."""
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        fail(f"--{field} must contain at least one value")
    return items


@models_app.command(name="list")
def list_models() -> None:
    """List registered models with enabled/priority/cache state.

    \b
    Examples:
      # Show the registry (reads ~/.config/openjev/config.json)
      openjev-tool models list

      # Point at another config file
      OPENJEV_CONFIG=/tmp/jev.json openjev-tool models list
    """
    registry = Registry.load()
    table = format_models_table(
        registry.config,
        {entry.id: entry_downloaded(entry) for entry in registry.config.models},
    )
    typer.echo(table)


@models_app.command(name="add")
def add_model(
    repo: Annotated[str, typer.Argument(help="Hugging Face repo id, e.g. AlexWortega/openjev.")],
    model_id: Annotated[
        str, typer.Option("--id", help="Registry id; defaults to the repo name lowercased.")
    ] = "",
    subfolder: Annotated[
        str, typer.Option("--subfolder", help="Subfolder inside the repo holding the weights.")
    ] = "",
    revision: Annotated[
        str, typer.Option("--revision", help="Pinned revision (branch/tag/sha).")
    ] = "main",
    labels: Annotated[
        str,
        typer.Option(
            "--labels",
            help="Comma-separated logit order (default: canonical order).",
        ),
    ] = ",".join(CANONICAL_LABELS),
    template: Annotated[
        str,
        typer.Option("--template", help="Prompt template with {premise} and {hypothesis}."),
    ] = DEFAULT_TEMPLATE,
    capabilities: Annotated[
        str,
        typer.Option(
            "--capabilities",
            help=f"Comma-separated capabilities (default: {','.join(DEFAULT_CAPABILITIES)}).",
        ),
    ] = ",".join(DEFAULT_CAPABILITIES),
    include_patterns: Annotated[
        str,
        typer.Option(
            "--include-patterns",
            help="Comma-separated allow-globs (e.g. '*.safetensors,*.json'); "
            "limits what is downloaded for root-layout repos.",
        ),
    ] = "",
    priority: Annotated[int, typer.Option("--priority", help="Router priority; higher wins.")] = 10,
    disabled: Annotated[
        bool, typer.Option("--disabled", help="Register but leave disabled (checkbox unticked).")
    ] = False,
) -> None:
    """Register a Hugging Face judgment model in the config.

    \b
    Examples:
      # Register an MNLI cross-encoder with its true logit order
      openjev-tool models add FacebookAI/roberta-large-mnli --id roberta-mnli \\
        --labels contradiction,neutral,entailment --capabilities noul,score --priority 5

      # Root-layout repo: fetch only the needed weights, not onnx/openvino variants
      openjev-tool models add cross-encoder/nli-deberta-v3-small \\
        --include-patterns '*.safetensors,*.json,*.txt'

      # Register disabled, download later
      openjev-tool models add some/org-jev --disabled
      openjev-tool models download some/org-jev
    """
    try:
        entry = ModelEntry.from_dict(
            {
                "id": model_id or repo.split("/")[-1].lower(),
                "repo": repo,
                "subfolder": subfolder,
                "revision": revision,
                "labels": list(_parse_csv(labels, "labels")),
                "template": template,
                "capabilities": list(_parse_csv(capabilities, "capabilities")),
                "include_patterns": (
                    list(_parse_csv(include_patterns, "include-patterns"))
                    if include_patterns
                    else []
                ),
                "priority": priority,
                "enabled": not disabled,
            }
        )
    except ConfigError as exc:
        fail(f"add failed: {exc}")
    registry = Registry.load()
    try:
        registry.add(entry)
    except RegistryError as exc:
        fail(f"add failed: {exc}")
    emit_json(entry.to_dict())


@models_app.command(name="remove")
def remove_model(
    model_id: Annotated[str, typer.Argument(help="Registry id of the model to remove.")],
) -> None:
    """Remove a model from the registry (weights stay in the HF cache).

    \b
    Examples:
      openjev-tool models remove roberta-large-mnli
    """
    registry = Registry.load()
    try:
        registry.remove(model_id)
    except RegistryError as exc:
        fail(f"remove failed: {exc}")
    emit_json({"removed": model_id})


@models_app.command(name="enable")
def enable_model(
    model_id: Annotated[str, typer.Argument(help="Registry id of the model to enable.")],
) -> None:
    """Enable a model (menu-bar checkbox ticked; router may pick it).

    \b
    Examples:
      openjev-tool models enable roberta-large-mnli
    """
    _set_enabled(model_id, True, "enable")


@models_app.command(name="disable")
def disable_model(
    model_id: Annotated[str, typer.Argument(help="Registry id of the model to disable.")],
) -> None:
    """Disable a model (menu-bar checkbox unticked; router skips it).

    \b
    Examples:
      openjev-tool models disable roberta-large-mnli
    """
    _set_enabled(model_id, False, "disable")


def _set_enabled(model_id: str, enabled: bool, action: str) -> None:
    """Shared enable/disable implementation (pure helper for the thin commands)."""
    registry = Registry.load()
    try:
        registry.set_enabled(model_id, enabled)
    except RegistryError as exc:
        fail(f"{action} failed: {exc}")
    emit_json({"id": model_id, "enabled": enabled})


@models_app.command(name="download")
def download_model(
    model_id: Annotated[str, typer.Argument(help="Registry id of the model to download.")],
    hf_transfer: Annotated[
        bool,
        typer.Option(
            "--hf-transfer",
            help="Use the Rust multi-chunk hf_transfer engine (faster, coarser progress).",
        ),
    ] = False,
) -> None:
    """Download a registered model with live progress (MB, throughput, ETA).

    \b
    Examples:
      # Fetch the small cross-encoder (~142 MB) with a live progress line
      openjev-tool models download nli-deberta-v3-small

      # Fetch the default openjev checkpoint (~8 GB)
      openjev-tool models download openjev-qwen3.5-4b

      # Rust multi-chunk engine (needs: uv add hf_transfer)
      openjev-tool models download openjev-qwen3.5-4b --hf-transfer

      # Then serve it
      openjev-tool serve

    Progress renders to stderr (bar, %, MB, MB/s, time left); the final JSON
    result goes to stdout so scripts and agents can parse it.
    """
    from openjev_tool.downloader import (
        HfTransferError,
        download_with_progress,
        enable_hf_transfer,
        render_progress,
    )

    if hf_transfer:
        try:
            enable_hf_transfer()
        except HfTransferError as exc:
            fail(f"download failed: {exc}")
    registry = Registry.load()
    try:
        entry = registry.entry(model_id)
    except RegistryError as exc:
        fail(f"download failed: {exc}")

    def _console(progress: object) -> None:
        typer.secho(f"\r{render_progress(progress)}", nl=False, err=True)  # type: ignore[arg-type]

    typer.secho(f"downloading {entry.repo}", err=True)
    try:
        path = download_with_progress(entry, [_console])
    except Exception as exc:
        fail(f"download failed: {exc}")
    typer.secho("", err=True)
    emit_json({"id": entry.id, "repo": entry.repo, "subfolder": entry.subfolder, "path": path})


@models_app.command(name="purge")
def purge_model(
    model_id: Annotated[
        str, typer.Argument(help="Registry id of the model to remove from the cache.")
    ],
) -> None:
    """Remove a model's weights from the HF cache (registry entry stays).

    \b
    Examples:
      # Free the ~8 GB openjev checkpoint (config entry remains, disabled or not)
      openjev-tool models purge openjev-qwen3.5-4b

      # Re-download later
      openjev-tool models download openjev-qwen3.5-4b
    """
    registry = Registry.load()
    try:
        entry = registry.entry(model_id)
    except RegistryError as exc:
        fail(f"purge failed: {exc}")
    freed = delete_entry_cache(entry)
    emit_json({"id": entry.id, "repo": entry.repo, "freed_bytes": freed})
