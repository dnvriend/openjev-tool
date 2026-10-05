"""Tests for stats collection and persistence (pure, tmp paths)."""

from __future__ import annotations

from pathlib import Path

from openjev_tool.stats import StatsCollector, percentile, state_dir, stats_path


def test_percentile_basic_and_empty() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0
    assert percentile([], 0.5) is None


def test_state_dir_honors_env(monkeypatch) -> None:
    monkeypatch.setenv("OPENJEV_STATE_DIR", "/tmp/openjev-state")
    assert state_dir() == Path("/tmp/openjev-state/openjev")
    assert stats_path() == Path("/tmp/openjev-state/openjev/stats.json")


def test_record_updates_endpoint_and_model_tallies() -> None:
    collector = StatsCollector()
    collector.record("score", "model-a", 0.010, error=False)
    collector.record("score", "model-a", 0.030, error=True)
    snapshot = collector.snapshot()
    assert snapshot["totals"] == {"invocations": 2, "errors": 1}
    assert snapshot["endpoints"]["score"]["count"] == 2
    assert snapshot["models"]["model-a"]["errors"] == 1
    assert snapshot["endpoints"]["score"]["p50_ms"] is not None


def test_model_summary_feeds_the_router() -> None:
    collector = StatsCollector()
    collector.record("noul", "model-a", 0.020)
    collector.record("noul", "model-a", 0.040, error=True)
    summary = collector.model_summary()
    assert summary["model-a"]["success_rate"] == 0.5
    assert summary["model-a"]["p50_ms"] is not None


def test_persist_round_trip(tmp_path: Path) -> None:
    target = tmp_path / "stats.json"
    collector = StatsCollector(persist_path=target)
    collector.record("score", "model-a", 0.010)
    collector.record("noul", "model-a", 0.020, error=True)
    collector.save()
    assert target.exists()

    revived = StatsCollector(persist_path=target)
    snapshot = revived.snapshot()
    assert snapshot["totals"]["invocations"] == 2
    assert snapshot["totals"]["errors"] == 1
    assert snapshot["endpoints"]["score"]["p50_ms"] is None


def test_corrupt_persisted_file_is_ignored(tmp_path: Path) -> None:
    target = tmp_path / "stats.json"
    target.write_text("{oops", encoding="utf-8")
    collector = StatsCollector(persist_path=target)
    assert collector.snapshot()["totals"]["invocations"] == 0
