"""Invocation statistics: counters, latency percentiles, persistence.

Records per endpoint and per model: invocation count, error count, and a
latency ring buffer (last 256 calls) for p50/p95. When persistence is on,
counts are loaded from and flushed to ``~/.local/state/openjev/stats.json``
(XDG_STATE_HOME and OPENJEV_STATE_DIR honored).
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RING_SIZE = 256
PERSIST_EVERY = 20


def state_dir() -> Path:
    """Return the openjev state directory (OPENJEV_STATE_DIR > XDG_STATE_HOME)."""
    override = os.environ.get("OPENJEV_STATE_DIR")
    if override:
        return Path(override).expanduser() / "openjev"
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "state"
    return root / "openjev"


def stats_path() -> Path:
    """Return the stats persistence file path."""
    return state_dir() / "stats.json"


def percentile(values: Iterable[float], fraction: float) -> float | None:
    """Nearest-rank percentile of ``values`` (0.0-1.0); None when empty.

    Args:
        values: Latency samples in any order (milliseconds or seconds —
            the unit is the caller's; this helper is unit-agnostic).
        fraction: Percentile fraction, e.g. 0.5 for p50, 0.95 for p95.

    Returns:
        The rounded percentile value, or None when there are no samples.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    rank = math.ceil(fraction * len(ordered))
    index = min(rank, len(ordered)) - 1
    return round(ordered[index], 3)


@dataclass
class Tally:
    """Counters plus a bounded latency ring for one endpoint or model."""

    count: int = 0
    errors: int = 0
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=RING_SIZE))

    def record(self, duration_s: float, error: bool) -> None:
        """Record one invocation."""
        self.count += 1
        if error:
            self.errors += 1
        self.latencies.append(duration_s * 1000.0)

    def seed(self, count: int, errors: int) -> None:
        """Fold persisted counters in (latencies are never persisted)."""
        self.count += count
        self.errors += errors

    def to_dict(self) -> dict[str, Any]:
        """Snapshot shape used by GET /stats."""
        return {
            "count": self.count,
            "errors": self.errors,
            "p50_ms": percentile(self.latencies, 0.5),
            "p95_ms": percentile(self.latencies, 0.95),
        }


class StatsCollector:
    """Aggregates invocation stats and (optionally) persists counts to disk."""

    def __init__(self, persist_path: Path | None = None) -> None:
        """Start a collector; loads persisted counters when the file exists."""
        self.started_at = time.time()
        self.by_endpoint: dict[str, Tally] = {}
        self.by_model: dict[str, Tally] = {}
        self.persist_path = persist_path
        self._records = 0
        if persist_path is not None and persist_path.exists():
            self._load()

    def record(self, endpoint: str, model_id: str, duration_s: float, error: bool = False) -> None:
        """Record one invocation against its endpoint and model tallies."""
        self.by_endpoint.setdefault(endpoint, Tally()).record(duration_s, error)
        self.by_model.setdefault(model_id, Tally()).record(duration_s, error)
        self._records += 1
        if self.persist_path is not None and self._records % PERSIST_EVERY == 0:
            self.save()

    def model_summary(self) -> dict[str, dict[str, float]]:
        """Per-model success rate and p50 latency for the router."""
        summary: dict[str, dict[str, float]] = {}
        for model_id, tally in self.by_model.items():
            success_rate = (tally.count - tally.errors) / tally.count if tally.count else 1.0
            p50 = percentile(tally.latencies, 0.5)
            summary[model_id] = {
                "success_rate": round(success_rate, 4),
                "p50_ms": p50 if p50 is not None else float("inf"),
            }
        return summary

    def snapshot(self, resident_models: int = 0) -> dict[str, Any]:
        """Full stats document for GET /stats."""
        invocations = sum(tally.count for tally in self.by_endpoint.values())
        errors = sum(tally.errors for tally in self.by_endpoint.values())
        return {
            "uptime_s": round(time.time() - self.started_at, 3),
            "totals": {"invocations": invocations, "errors": errors},
            "endpoints": {name: tally.to_dict() for name, tally in self.by_endpoint.items()},
            "models": {name: tally.to_dict() for name, tally in self.by_model.items()},
            "resident_models": resident_models,
        }

    def save(self) -> None:
        """Persist endpoint/model counters (never latencies) as JSON."""
        if self.persist_path is None:
            return
        self.persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "endpoints": {
                name: {"count": t.count, "errors": t.errors} for name, t in self.by_endpoint.items()
            },
            "models": {
                name: {"count": t.count, "errors": t.errors} for name, t in self.by_model.items()
            },
        }
        self.persist_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _load(self) -> None:
        """Seed tallies from a previously persisted stats file."""
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
        except json.JSONDecodeError, OSError:
            return
        for section, target in (("endpoints", self.by_endpoint), ("models", self.by_model)):
            for name, counters in data.get(section, {}).items():
                target.setdefault(str(name), Tally()).seed(
                    int(counters.get("count", 0)), int(counters.get("errors", 0))
                )
