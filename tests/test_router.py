"""Tests for the deterministic router (pure)."""

from __future__ import annotations

import pytest

from openjev_tool.config import ModelEntry
from openjev_tool.router import RouterError, route


def _entries() -> list[ModelEntry]:
    return [
        ModelEntry(id="big", repo="org/big", priority=10, enabled=True),
        ModelEntry(id="small", repo="org/small", priority=5, enabled=True),
        ModelEntry(id="off", repo="org/off", priority=99, enabled=False),
    ]


def test_route_filters_by_capability_and_enabled() -> None:
    entries = [ModelEntry(id="noul-only", repo="org/x", capabilities=("noul",))]
    decision = route("noul", entries)
    assert decision.model_id == "noul-only"
    with pytest.raises(RouterError, match="no enabled model supports 'score'"):
        route("score", entries)


def test_route_skips_disabled_even_at_higher_priority() -> None:
    decision = route("score", _entries())
    assert decision.model_id == "big"
    assert "off" not in decision.candidates


def test_route_prefers_higher_priority() -> None:
    decision = route("rerank", _entries())
    assert decision.model_id == "big"
    assert decision.candidates == ("big", "small")


def test_route_reason_mentions_priority() -> None:
    decision = route("grade", _entries())
    assert decision.reason.startswith("priority=10")


def test_route_breaks_ties_with_stats() -> None:
    low, high = (
        ModelEntry(id="low", repo="org/low", priority=5),
        ModelEntry(id="high", repo="org/high", priority=5),
    )
    summary = {
        "low": {"success_rate": 0.99, "p50_ms": 100.0},
        "high": {"success_rate": 0.99, "p50_ms": 40.0},
    }
    decision = route("score", [low, high], summary)
    assert decision.model_id == "high"
    assert "p50=40ms" in decision.reason


def test_route_unknown_model_stats_ignored() -> None:
    decision = route("noul", _entries(), {"ghost": {"success_rate": 1.0}})
    assert decision.model_id == "big"
