"""Observable model downloads: progress snapshots with throughput and ETA.

huggingface_hub funnels all per-file byte progress (http chunks, xet
reconstruction, resumed offsets) into tqdm bars it instantiates from the
``tqdm_class`` passed to ``snapshot_download``. :class:`ObserverTqdm` bridges
those bars into a :class:`DownloadTracker`, which publishes immutable
:class:`DownloadProgress` snapshots (bytes done/total, windowed throughput,
ETA) to observer callbacks — tqdm-style observability, but rendering to any
output (menu bar item, console) instead of a terminal bar.

Transfers: hf-xet (chunked, concurrent, content-addressed) is the engine in
huggingface_hub >= 1.0. Its smooth byte counter is the ``"Downloading bytes"``
bar (the ``Reconstructing`` bar only moves per finished file), so that bar is
preferred; plain HTTP file bars are the fallback for non-xet repos.
``HF_XET_HIGH_PERFORMANCE=1`` is set to allow maximum concurrency.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from tqdm import tqdm

if TYPE_CHECKING:
    _BaseTqdm = tqdm[Any]
else:
    _BaseTqdm = tqdm

from openjev_tool.config import ModelEntry
from openjev_tool.registry import entry_patterns

THROUGHPUT_WINDOW_S = 8.0

ProgressObserver = Callable[["DownloadProgress"], None]


class HfTransferError(RuntimeError):
    """Raised when hf_transfer acceleration is requested but not installed."""


@dataclass(frozen=True, slots=True)
class DownloadProgress:
    """One immutable progress snapshot of a model download."""

    label: str
    done_bytes: int
    total_bytes: int | None
    elapsed_s: float
    throughput_bps: float = 0.0

    @property
    def fraction(self) -> float:
        """Done/total clamped to 0..1 (0.0 when the total is unknown)."""
        if not self.total_bytes:
            return 0.0
        return min(1.0, self.done_bytes / self.total_bytes)

    @property
    def eta_s(self) -> float | None:
        """Seconds remaining at the current throughput (None when unknown)."""
        if not self.total_bytes or self.throughput_bps <= 0:
            return None
        return max(0.0, (self.total_bytes - self.done_bytes) / self.throughput_bps)


def format_seconds(seconds: float) -> str:
    """Compact duration: 45s, 12m 05s, 1h05m."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def format_size(num_bytes: float) -> str:
    """Human-readable size: B, KB, MB, GB (decimal)."""
    value = max(float(num_bytes), 0.0)
    for unit in ("B", "KB", "MB"):
        if value < 1000:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.2f} GB"


def format_rate(bytes_per_s: float) -> str:
    """Throughput such as ``48.2 MB/s``."""
    return f"{format_size(bytes_per_s)}/s"


def render_bar(fraction: float, width: int = 20) -> str:
    """Unicode block bar with 1/8-cell precision."""
    fraction = min(1.0, max(0.0, fraction))
    eighths = round(fraction * width * 8)
    full, part = divmod(eighths, 8)
    partial = " ▏▎▍▌▋▊▉"[part] if part and full < width else ""
    return ("█" * full + partial).ljust(width, "░")[:width]


def render_title(progress: DownloadProgress) -> str:
    """Compact menu bar title: ``⬇ 47% · 48.2 MB/s``."""
    if not progress.total_bytes:
        return f"⬇ {format_size(progress.done_bytes)}"
    title = f"⬇ {int(progress.fraction * 100)}%"
    if progress.throughput_bps > 0:
        title += f" · {format_rate(progress.throughput_bps)}"
    return title


def render_lines(progress: DownloadProgress, bar_width: int = 24) -> tuple[str, str, str]:
    """Three display lines: headline+bar, size, speed/time left."""
    percent = f"{int(progress.fraction * 100)}%" if progress.total_bytes else "…"
    head = f"{progress.label}  {render_bar(progress.fraction, bar_width)}  {percent}"
    if progress.total_bytes:
        size = f"{format_size(progress.done_bytes)} of {format_size(progress.total_bytes)}"
    else:
        size = f"{format_size(progress.done_bytes)} downloaded"
    parts = [format_rate(progress.throughput_bps)] if progress.throughput_bps > 0 else ["starting…"]
    eta = progress.eta_s
    if eta is not None:
        parts.append(f"{format_seconds(eta)} left")
    return head, size, " · ".join(parts)


def render_progress(progress: DownloadProgress, bar_width: int = 10) -> str:
    """One-line render: bar, percent, size, throughput, remaining time."""
    line = f"⬇ {progress.label} "
    if progress.total_bytes:
        line += (
            f"{render_bar(progress.fraction, bar_width)} {int(progress.fraction * 100)}% "
            f"{format_size(progress.done_bytes)}/{format_size(progress.total_bytes)}"
        )
    else:
        line += format_size(progress.done_bytes)
    if progress.throughput_bps > 0:
        line += f" · {format_rate(progress.throughput_bps)}"
    eta = progress.eta_s
    if eta is not None:
        line += f" · {format_seconds(eta)} left"
    return line


class DownloadTracker:
    """Aggregates observed bar updates into progress snapshots.

    Thread-safe: huggingface_hub downloads files from a thread pool, so
    observer bridges call :meth:`publish` from worker threads. Observer
    emission is throttled to ``min_interval_s``; samples stay unthrottled so
    throughput windows stay accurate.
    """

    def __init__(
        self,
        label: str,
        observers: Iterable[ProgressObserver] = (),
        clock: Callable[[], float] = time.monotonic,
        min_interval_s: float = 0.1,
    ) -> None:
        """Start a tracker; ``clock`` is injectable for tests."""
        self.label = label
        self._observers = list(observers)
        self._clock = clock
        self._min_interval_s = min_interval_s
        self._started = clock()
        self._last_emit = float("-inf")
        self._lock = threading.Lock()
        self._bridges: set[Any] = set()
        self._finished: list[tuple[bool, int, int]] = []
        self._samples: deque[tuple[float, int]] = deque(maxlen=1024)

    def register(self, bridge: Any) -> None:
        """Track a progress bar bridge."""
        with self._lock:
            self._bridges.add(bridge)

    def unregister(self, bridge: Any) -> None:
        """Publish the final state, then retire the bridge (its bytes stay counted)."""
        self.publish()
        with self._lock:
            if bridge in self._bridges:
                self._bridges.discard(bridge)
                self._finished.append(
                    (bool(getattr(bridge, "primary", False)), int(bridge.n), int(bridge.total or 0))
                )

    def snapshot(self) -> DownloadProgress:
        """Current aggregate snapshot with windowed throughput."""
        now = self._clock()
        with self._lock:
            rows = [
                (bool(getattr(b, "primary", False)), int(b.n), int(b.total or 0))
                for b in self._bridges
            ] + self._finished
            samples = list(self._samples)
        # Prefer the smooth xet transfer counter; fall back to per-file HTTP bars.
        primaries = [row for row in rows if row[0]]
        chosen = primaries or rows
        done = sum(row[1] for row in chosen)
        known_total = sum(row[2] for row in chosen)
        total = known_total if known_total else None
        throughput = self._windowed_throughput(done, now, samples)
        return DownloadProgress(
            label=self.label,
            done_bytes=done,
            total_bytes=total,
            elapsed_s=now - self._started,
            throughput_bps=throughput,
        )

    def publish(self) -> None:
        """Push a snapshot to all observers, throttled; errors are swallowed."""
        snapshot = self.snapshot()
        now = self._clock()
        with self._lock:
            self._samples.append((now, snapshot.done_bytes))
            while self._samples and now - self._samples[0][0] > THROUGHPUT_WINDOW_S:
                self._samples.popleft()
            observers = list(self._observers)
        with self._lock:
            if now - self._last_emit < self._min_interval_s:
                return
            self._last_emit = now
        for observer in observers:
            try:
                observer(snapshot)
            except Exception:  # nosec B112 — a broken observer must not kill the download
                continue

    def _windowed_throughput(
        self, done_now: int, now: float, samples: list[tuple[float, int]]
    ) -> float:
        """Bytes/second over the recent window, falling back to the average."""
        recent = [(stamp, done) for stamp, done in samples if now - stamp <= THROUGHPUT_WINDOW_S]
        if len(recent) >= 2:
            first_stamp, first_done = recent[0]
            span = now - first_stamp
            if span > 0.5:
                return max(0.0, (done_now - first_done) / span)
        elapsed = now - self._started
        return done_now / elapsed if elapsed > 0 else 0.0


class ObserverTqdm(_BaseTqdm):
    """Bridge huggingface_hub progress bars into a :class:`DownloadTracker`.

    Only byte bars are tracked (``unit="B"``). The xet ``"Downloading bytes"``
    bar is *primary* (smooth, real network bytes); the ``Reconstructing`` bar
    and ``.transfer`` bars are skipped to avoid double counting. Display is
    always disabled — observers render instead.
    """

    def __init__(self, *args: Any, tracker: DownloadTracker, **kwargs: Any) -> None:
        """Wire the bar to ``tracker``; see :class:`tqdm` for other kwargs."""
        kwargs["disable"] = True
        super().__init__(*args, **kwargs)
        self._tracker = tracker
        name = str(kwargs.get("name", ""))
        desc = str(kwargs.get("desc", ""))
        self.primary = desc == "Downloading bytes"
        self._tracked = bool(
            kwargs.get("unit") == "B"
            and not name.endswith(".transfer")
            and not desc.startswith("Reconstructing")
        )
        if self._tracked:
            tracker.register(self)

    def update(self, n: float | None = 1) -> bool | None:
        """Track bytes and publish an aggregate snapshot.

        A disabled tqdm skips counting entirely, so bytes are accumulated
        manually here — display stays suppressed, counters keep working.
        """
        if self.disable:
            if n:
                self.n += n
            if self._tracked:
                self._tracker.publish()
            return None
        result = super().update(n)
        if self._tracked:
            self._tracker.publish()
        return result

    def close(self) -> None:
        """Unregister before closing so final states are published."""
        if self._tracked:
            self._tracker.unregister(self)
        super().close()


def download_with_progress(
    entry: ModelEntry,
    observers: Iterable[ProgressObserver] = (),
    tracker: DownloadTracker | None = None,
) -> str:
    """Download the entry's checkpoint, publishing progress to ``observers``.

    Args:
        entry: The registry entry to download.
        observers: Called with :class:`DownloadProgress` snapshots.
        tracker: Existing tracker to feed (e.g. polled by a UI); created when None.

    Returns:
        The local snapshot path from huggingface_hub.

    Raises:
        Exception: Whatever huggingface_hub raises on network/auth failure.
    """
    from huggingface_hub import snapshot_download

    os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    if tracker is None:
        tracker = DownloadTracker(entry.id, observers)
    bound = tracker

    class _Bridge(ObserverTqdm):
        """Per-download class binding the tracker (HF passes name only to classes)."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, tracker=bound, **kwargs)

    return str(
        snapshot_download(
            repo_id=entry.repo,
            allow_patterns=entry_patterns(entry),
            revision=entry.revision,
            tqdm_class=cast(Any, _Bridge),
        )
    )


def enable_hf_transfer() -> None:
    """Opt into the Rust multi-chunk hf_transfer download engine.

    Raises:
        HfTransferError: When the ``hf_transfer`` package is not installed.
    """
    if importlib.util.find_spec("hf_transfer") is None:
        raise HfTransferError(
            "hf_transfer is not installed — add it with `uv add hf_transfer`"
            " (or `uv tool install . --with hf_transfer` for the global tool)"
        )
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
