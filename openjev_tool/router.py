"""Deterministic model router: capability -> priority -> live stats.

Pure function over config entries plus a stats summary: filter models whose
capabilities include the endpoint and that are enabled, order by configured
priority (desc), then success rate (desc), then p50 latency (asc). The
decision carries a human-readable reason so routing is explainable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from openjev_tool.config import ModelEntry


class RouterError(ValueError):
    """Raised when no enabled model can serve the requested endpoint."""


@dataclass(frozen=True)
class RoutingDecision:
    """The chosen model plus the explanation and the full candidate order."""

    model_id: str
    reason: str
    candidates: tuple[str, ...]


def _sort_key(
    entry: ModelEntry, summary: Mapping[str, Mapping[str, float]]
) -> tuple[int, float, float]:
    stats = summary.get(entry.id, {})
    success_rate = stats.get("success_rate", 1.0)
    p50_ms = stats.get("p50_ms", float("inf"))
    return (-entry.priority, -float(success_rate), float(p50_ms))


def route(
    endpoint: str,
    entries: Sequence[ModelEntry],
    summary: Mapping[str, Mapping[str, float]] | None = None,
) -> RoutingDecision:
    """Pick the model that should serve ``endpoint``.

    Args:
        endpoint: One of score/noul/choice/rerank/grade.
        entries: The registry entries (enabled and disabled).
        summary: Optional per-model stats (``StatsCollector.model_summary``).

    Returns:
        A RoutingDecision with the winner, the reason, and all candidates.

    Raises:
        RouterError: When no enabled entry supports the endpoint.
    """
    capable = [entry for entry in entries if entry.enabled and entry.supports(endpoint)]
    if not capable:
        raise RouterError(
            f"no enabled model supports '{endpoint}' — "
            "enable one via `openjev-tool models enable <id>`"
        )
    live_summary = summary or {}
    ordered = sorted(capable, key=lambda entry: _sort_key(entry, live_summary))
    best = ordered[0]
    reason = f"priority={best.priority}"
    stats = live_summary.get(best.id)
    if stats:
        success = stats.get("success_rate", 1.0)
        p50 = stats.get("p50_ms", float("inf"))
        reason += f",success={success:.2f},p50={p50:.0f}ms"
    return RoutingDecision(
        model_id=best.id,
        reason=reason,
        candidates=tuple(entry.id for entry in ordered),
    )
