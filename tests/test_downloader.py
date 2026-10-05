"""Tests for the observable downloader (no network)."""

from __future__ import annotations

from typing import Any

import pytest

from openjev_tool.config import ModelEntry
from openjev_tool.downloader import (
    DownloadProgress,
    DownloadTracker,
    HfTransferError,
    ObserverTqdm,
    download_with_progress,
    enable_hf_transfer,
    format_seconds,
    render_progress,
)


def test_format_seconds() -> None:
    assert format_seconds(0) == "0s"
    assert format_seconds(45) == "45s"
    assert format_seconds(725) == "12m 05s"
    assert format_seconds(3900) == "1h05m"


def test_download_progress_properties() -> None:
    progress = DownloadProgress("m", 50, 100, 10.0, throughput_bps=5.0)
    assert progress.fraction == 0.5
    assert progress.eta_s == 10.0
    unknown = DownloadProgress("m", 50, None, 10.0)
    assert unknown.fraction == 0.0
    assert unknown.eta_s is None


def test_render_progress_full_and_partial() -> None:
    full = render_progress(
        DownloadProgress("m", 62_000_000, 100_000_000, 20.0, throughput_bps=1_000_000)
    )
    assert "62%" in full
    assert "█" in full and "░" in full
    assert "62.0 MB/100.0 MB" in full
    assert "1.0 MB/s" in full
    assert "left" in full
    bare = render_progress(DownloadProgress("m", 300_000_000, None, 5.0))
    assert bare == "⬇ m 300.0 MB"
    stalled = render_progress(DownloadProgress("m", 0, 100, 0.0))
    assert stalled == "⬇ m ░░░░░░░░░░ 0% 0 B/100 B"


def test_tracker_aggregates_bridges_with_windowed_throughput() -> None:
    clock_values = [0.0]
    received: list[DownloadProgress] = []
    tracker = DownloadTracker("m", observers=[received.append], clock=lambda: clock_values[0])

    class _Bridge:
        def __init__(self) -> None:
            self.n = 0
            self.total = 100

    bridge = _Bridge()
    tracker.register(bridge)

    bridge.n = 10
    tracker.publish()
    clock_values[0] = 4.0
    bridge.n = 30
    tracker.publish()
    clock_values[0] = 8.0
    bridge.n = 50
    tracker.publish()

    last = received[-1]
    assert last.done_bytes == 50
    assert last.total_bytes == 100
    assert last.throughput_bps == pytest.approx(5.0)
    assert last.eta_s == pytest.approx(10.0)


def test_observer_tqdm_tracks_byte_bars_only() -> None:
    received: list[DownloadProgress] = []
    tracker = DownloadTracker("m", observers=[received.append])

    byte_bar = ObserverTqdm(
        tracker=tracker, total=100, unit="B", name="huggingface_hub.snapshot_download"
    )
    byte_bar.update(40)
    assert received[-1].done_bytes == 40
    assert received[-1].total_bytes == 100

    transfer_bar = ObserverTqdm(
        tracker=tracker, total=100, unit="B", name="huggingface_hub.snapshot_download.transfer"
    )
    files_bar = ObserverTqdm(tracker=tracker, total=3, unit="files", name="thread_map")
    baseline = received[-1].done_bytes
    transfer_bar.update(10)
    files_bar.update(1)
    assert received[-1].done_bytes == baseline

    byte_bar.close()
    assert received[-1].done_bytes == 40
    # finished bars stay counted so progress never regresses
    assert tracker.snapshot().done_bytes == 40


def test_download_with_progress_wires_tqdm_class(monkeypatch: pytest.MonkeyPatch) -> None:
    import huggingface_hub

    captured: dict[str, Any] = {}

    def _fake_snapshot_download(**kwargs: Any) -> str:
        captured.update(kwargs)
        return "/cache/models--org--small"

    monkeypatch.setattr(huggingface_hub, "snapshot_download", _fake_snapshot_download)
    entry = ModelEntry(id="small", repo="org/small", subfolder="weights")
    received: list[DownloadProgress] = []
    path = download_with_progress(entry, [received.append])

    assert path == "/cache/models--org--small"
    assert captured["repo_id"] == "org/small"
    assert captured["allow_patterns"] == ["weights/*", "*.md", "LICENSE*"]
    factory = captured["tqdm_class"]
    bar = factory(total=100, unit="B", name="huggingface_hub.snapshot_download")
    bar.update(25)
    assert received[-1].done_bytes == 25
    bar.close()


def test_enable_hf_transfer_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjev_tool.downloader as downloader

    monkeypatch.setattr(downloader.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(HfTransferError, match="hf_transfer"):
        enable_hf_transfer()


def test_tracker_prefers_xet_downloading_bytes_bar() -> None:
    tracker = DownloadTracker("m", min_interval_s=0.0)
    reconstruct = ObserverTqdm(
        tracker=tracker, total=1000, unit="B", desc="Reconstructing (incomplete total...)"
    )
    xet = ObserverTqdm(tracker=tracker, total=400, unit="B", desc="Downloading bytes")
    xet.update(100)
    reconstruct.update(1000)
    snap = tracker.snapshot()
    assert (snap.done_bytes, snap.total_bytes) == (100, 400)


def test_render_helpers() -> None:
    from openjev_tool.downloader import (
        format_rate,
        format_size,
        render_bar,
        render_lines,
        render_title,
    )

    assert format_size(2_500_000_000) == "2.50 GB"
    assert format_rate(48_200_000) == "48.2 MB/s"
    assert render_bar(0.5, 10) == "█████░░░░░"
    assert len(render_bar(0.33, 10)) == 10
    progress = DownloadProgress("m", 50, 100, 10.0, throughput_bps=5.0)
    head, size, speed = render_lines(progress)
    assert "50%" in head and "50 B of 100 B" == size and "5 B/s" in speed and "10s left" in speed
    assert render_title(progress) == "⬇ 50% · 5 B/s"
