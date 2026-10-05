"""macOS menu bar controller (rumps) for the openjev server.

Menu bar states: ``○ jev`` stopped, ``◐ jev`` starting, ``● jev`` running,
``✕ jev`` error. Start/Stop supervises a child
``openjev-tool serve --supervised`` process and attaches to an already-running
server instead of spawning a second one. Model checkboxes write
``~/.config/openjev/config.json`` and hot-reload a running server via
``GET /v1/reload``. Models show a ``· cached`` / ``· not cached`` marker; the
Download menu runs an in-process observable download; while it runs the menu
bar title shows ``⬇ 47% · 48 MB/s`` and the menu gains a progress row (native
``NSProgressIndicator`` with size, speed and time left).
Notifications degrade to alerts when no bundle ``Info.plist`` is present.
Pure helpers are unit-tested; the rumps glue is smoke-tested manually
(rumps is an optional ``[menubar]`` extra).
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 — fixed argv supervisor, no shell (see spawn_server)
import sys
import threading
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib import request as urllib_request

from openjev_tool.config import Config, ModelEntry, load_config
from openjev_tool.downloader import render_lines, render_title
from openjev_tool.registry import Registry, delete_entry_cache, entry_downloaded

POLL_SECONDS = 3
BUNDLE_ID = "ai.openjev.tool"

TITLES: dict[str, str] = {
    "stopped": "○ jev",
    "starting": "◐ jev",
    "running": "● jev",
    "error": "✕ jev",
}


def server_url(config: Config) -> str:
    """The base URL of the configured server."""
    return f"http://{config.server.host}:{config.server.port}"


def fetch_json(base_url: str, path: str, timeout: float = 1.5) -> dict[str, Any] | None:
    """GET ``base_url/path`` as JSON; None on any failure (offline, error)."""
    try:
        with urllib_request.urlopen(base_url + path, timeout=timeout) as response:  # nosec B310
            data = json.loads(response.read().decode())
        return dict(data) if isinstance(data, dict) else None
    except Exception:
        return None


def format_stats_line(stats: Mapping[str, Any] | None) -> str:
    """One-line stats summary for the menu: invocations, errors, worst p50."""
    if not stats:
        return "server offline"
    totals = stats.get("totals", {})
    invocations = int(totals.get("invocations", 0))
    errors = int(totals.get("errors", 0))
    p50 = max(
        (float(tally.get("p50_ms") or 0) for tally in stats.get("endpoints", {}).values()),
        default=0.0,
    )
    line = f"{invocations:,} invocations · {errors} errors"
    if p50:
        line += f" · p50 {p50:.0f}ms"
    return line


class ProgressRow:
    """Native menu row: title, determinate progress bar, and detail line.

    Built on AppKit (pyobjc, a rumps dependency). :meth:`update` must run on
    the main thread (rumps timers do).
    """

    WIDTH = 300.0
    HEIGHT = 62.0

    def __init__(self) -> None:
        """Create the view hierarchy (raises ImportError outside macOS)."""
        from AppKit import (  # type: ignore[import-untyped,import-not-found,unused-ignore]
            NSColor,
            NSFont,
            NSProgressIndicator,
            NSProgressIndicatorStyleBar,
            NSTextField,
            NSView,
        )
        from Foundation import NSMakeRect  # type: ignore[import-untyped]

        pad = 16.0
        inner = self.WIDTH - 2 * pad
        self.view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.WIDTH, self.HEIGHT))

        def label(rect: Any, size: float, bold: bool, secondary: bool) -> Any:
            field = NSTextField.alloc().initWithFrame_(rect)
            field.setBezeled_(False)
            field.setDrawsBackground_(False)
            field.setEditable_(False)
            field.setSelectable_(False)
            field.setFont_(
                NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size)
            )
            if secondary:
                field.setTextColor_(NSColor.secondaryLabelColor())
            self.view.addSubview_(field)
            return field

        self.title = label(NSMakeRect(pad, 42, inner, 16), 13, True, False)
        self.bar = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(pad, 28, inner, 10))
        self.bar.setStyle_(NSProgressIndicatorStyleBar)
        self.bar.setIndeterminate_(False)
        self.bar.setMinValue_(0.0)
        self.bar.setMaxValue_(1.0)
        self.view.addSubview_(self.bar)
        self.size_line = label(NSMakeRect(pad, 8, inner * 0.55, 14), 11, False, True)
        self.speed_line = label(
            NSMakeRect(pad + inner * 0.55, 8, inner * 0.45, 14), 11, False, True
        )
        self.speed_line.setAlignment_(1)  # NSTextAlignmentRight

    def update(self, progress: Any) -> None:
        """Render a :class:`~openjev_tool.downloader.DownloadProgress` snapshot."""
        _head, size, speed = render_lines(progress)
        self.title.setStringValue_(f"Downloading {progress.label}")
        if progress.total_bytes:
            self.bar.stopAnimation_(None)
            self.bar.setIndeterminate_(False)
            self.bar.setDoubleValue_(progress.fraction)
        else:
            self.bar.setIndeterminate_(True)
            self.bar.startAnimation_(None)
        self.size_line.setStringValue_(size)
        self.speed_line.setStringValue_(speed)


def state_title(state: str) -> str:
    """Menu bar title for a server state."""
    try:
        return TITLES[state]
    except KeyError as exc:
        raise ValueError(f"unknown state: {state!r}") from exc


def format_model_title(entry: ModelEntry, cached: bool) -> str:
    """Models-menu row title: id plus a monochrome cache marker."""
    return f"{entry.id} · {'cached' if cached else 'not cached'}"


def format_bytes(num_bytes: int) -> str:
    """Human-readable size: GB from 1 GB, else MB."""
    gigabytes = num_bytes / 1_000_000_000
    if gigabytes >= 1:
        return f"{gigabytes:.1f} GB"
    return f"{max(num_bytes, 0) / 1_000_000:.0f} MB"


def repo_cache_bytes(repo_id: str) -> int:
    """Bytes the repo occupies in the local HF cache (0 on any failure)."""
    try:
        from huggingface_hub import scan_cache_dir

        cache: Any = scan_cache_dir()
        return sum(entry.size_on_disk for entry in cache.repos if entry.repo_id == repo_id)
    except Exception:
        return 0


def spawn_server(fake: bool = False) -> subprocess.Popen[bytes]:
    """Spawn ``openjev-tool serve --supervised`` as a child process."""
    args = [sys.executable, "-m", "openjev_tool.cli", "serve", "--supervised"]
    if fake:
        args.append("--fake")
    return subprocess.Popen(  # nosec B603 — fixed argv, no shell
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def terminate_process(process: subprocess.Popen[bytes] | None) -> None:
    """Terminate a child process: SIGTERM, grace, SIGKILL."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def terminate_server(process: subprocess.Popen[bytes] | None) -> None:
    """Terminate a supervised server child (see :func:`terminate_process`)."""
    terminate_process(process)


def reload_running_server(base_url: str) -> bool:
    """Ask a running server to re-read its config; True on success."""
    try:
        urllib_request.urlopen(base_url + "/v1/reload", timeout=5).read()  # nosec B310
        return True
    except Exception:
        return False


def config_mtime() -> float:
    """mtime of the config file (0 when missing) for cheap change detection."""
    try:
        return load_config_path().stat().st_mtime
    except OSError:
        return 0.0


def load_config_path() -> Path:
    """The active config path (honors OPENJEV_CONFIG/XDG)."""
    from openjev_tool.config import config_path

    return config_path()


def ensure_notification_plist() -> None:
    """Write the bundle Info.plist rumps notifications require, if missing.

    rumps raises RuntimeError when ``sys.executable`` has no neighboring
    ``Info.plist`` with a ``CFBundleIdentifier`` (i.e. outside a .app bundle).
    Writing one next to the venv interpreter enables real notifications.
    """
    try:
        target = Path(sys.executable).parent / "Info.plist"
        if target.exists():
            return
        target.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
            ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            f"  <dict><key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string></dict>\n"
            "</plist>\n",
            encoding="utf-8",
        )
    except OSError:
        return


def run_menubar(fake: bool = False) -> None:
    """Build and run the rumps menu bar app (blocking; macOS only).

    Args:
        fake: Pass ``--fake`` to the supervised server (FakeBackend).

    Raises:
        SystemExit: When the optional ``[menubar]`` extra (rumps) is missing.
    """
    try:
        import rumps
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "rumps is not installed in this environment — the menu bar needs"
            " the optional [menubar] extra.\n"
            "  dev venv:    uv sync --extra menubar\n"
            "  global tool: uv tool install . --reinstall --force --with rumps"
        ) from exc

    class OpenJevMenuBar(rumps.App):  # type: ignore[misc]
        """Toolbar controller: status, start/stop, models, downloads, stats."""

        def __init__(self) -> None:
            super().__init__(
                name="openjev",
                title=state_title("stopped"),
                quit_button=None,
            )
            self.fake = fake
            self.child: subprocess.Popen[bytes] | None = None
            self.state = "stopped"
            self._config_seen = -1.0
            self.download_thread: threading.Thread | None = None
            self.download_tracker: Any = None
            self.download_id = ""
            self.download_error: str | None = None
            self.download_items: dict[str, Any] = {}
            self.progress_row: ProgressRow | None = None
            self.progress_item = rumps.MenuItem("Downloading…")
            try:
                self.progress_row = ProgressRow()
                self.progress_item._menuitem.setView_(self.progress_row.view)
            except Exception:  # no AppKit view support: fall back to a text row
                self.progress_row = None
            self.status_item = rumps.MenuItem("Stopped")
            self.toggle_item = rumps.MenuItem("Start Server", callback=self.on_toggle)
            self.open_item = rumps.MenuItem("Open Playground", callback=self.on_open)
            self.models_item = rumps.MenuItem("Models")
            self.add_model_item = rumps.MenuItem("Add Model…", callback=self.on_add_model)
            self.downloads_item = rumps.MenuItem("Download")
            self.deletes_item = rumps.MenuItem("Delete from cache")
            self.stats_item = rumps.MenuItem("server offline")
            self.settings_item = rumps.MenuItem("Server Settings…", callback=self.on_open)
            self.quit_item = rumps.MenuItem("Quit", callback=self.on_quit)
            from openjev_tool.config import ensure_config

            ensure_config()
            ensure_notification_plist()
            self._rebuild_menu()
            rumps.Timer(self.on_tick, POLL_SECONDS).start()
            rumps.Timer(self._tick_progress, 1).start()

        @property
        def base_url(self) -> str:
            return server_url(load_config())

        def _notify(self, title: str, subtitle: str, message: str) -> None:
            """Notify via the notification center; degrade to alert, then mute."""
            try:
                rumps.notification(title, subtitle, message)
            except Exception:
                try:
                    rumps.alert(f"{title} — {subtitle}", message)
                except Exception:
                    return

        def _rebuild_menu(self) -> None:
            config = load_config()
            self.menu.clear()
            self.models_item = rumps.MenuItem("Models")
            self.downloads_item = rumps.MenuItem("Download")
            self.deletes_item = rumps.MenuItem("Delete from cache")
            self.download_items = {}
            cached_any = False
            for entry in config.models:
                cached = entry_downloaded(entry)
                item = rumps.MenuItem(
                    format_model_title(entry, cached), callback=self.on_model_toggle
                )
                item.state = entry.enabled
                self.models_item.add(item)
                if not cached:
                    download_item = rumps.MenuItem(
                        entry.id, callback=self._make_download_callback(entry.id)
                    )
                    self.downloads_item.add(download_item)
                    self.download_items[entry.id] = download_item
                else:
                    cached_any = True
                    delete_item = rumps.MenuItem(
                        f"{entry.id} ({format_bytes(repo_cache_bytes(entry.repo))})",
                        callback=self._make_delete_callback(entry.id),
                    )
                    self.deletes_item.add(delete_item)
            if not self.download_items:
                self.downloads_item.add(rumps.MenuItem("(all models cached)"))
            if not cached_any:
                self.deletes_item.add(rumps.MenuItem("(nothing cached)"))
            downloading = self.download_thread is not None
            self.menu.update(
                [
                    self.status_item,
                    *([self.progress_item] if downloading else []),
                    None,
                    self.toggle_item,
                    self.open_item,
                    None,
                    self.models_item,
                    self.add_model_item,
                    self.downloads_item,
                    self.deletes_item,
                    None,
                    self.stats_item,
                    self.settings_item,
                    None,
                    self.quit_item,
                ]
            )

        def _make_download_callback(self, model_id: str) -> Callable[[object], None]:
            def _download(_sender: object) -> None:
                self.on_download(model_id)

            return _download

        def _make_delete_callback(self, model_id: str) -> Callable[[object], None]:
            def _delete(_sender: object) -> None:
                self.on_delete(model_id)

            return _delete

        def on_quit(self, _sender: object) -> None:
            """Stop the supervised server child and quit (downloads resume later)."""
            terminate_process(self.child)
            self.child = None
            rumps.quit_application()

        def on_delete(self, model_id: str) -> None:
            """Confirm, then remove the model's weights from the HF cache."""
            downloading = (
                self.download_thread is not None
                and self.download_thread.is_alive()
                and self.download_id == model_id
            )
            if downloading:
                self._notify("openjev", "Delete blocked", "A download is running for this model.")
                return
            entry = next((item for item in load_config().models if item.id == model_id), None)
            if entry is None:
                return
            confirmed = rumps.alert(
                f"Remove {entry.repo} weights?",
                "Deletes the local Hugging Face cache copy; the registry entry stays.",
                ok="Delete",
                cancel=True,
            )
            if confirmed != 1:
                return
            freed = delete_entry_cache(entry)
            self._config_seen = -1.0
            self._notify("openjev", "Cache cleared", f"{model_id} — freed {format_bytes(freed)}")

        def _set_state(self, state: str) -> None:
            self.state = state
            if self.download_thread is None:
                self.title = state_title(state)
            self.status_item.title = {
                "stopped": "Stopped",
                "starting": "Starting…",
                "running": f"Running — {self.base_url}",
                "error": "Server exited unexpectedly",
            }[state]
            self.toggle_item.title = "Stop Server" if state == "running" else "Start Server"

        def on_toggle(self, _sender: object) -> None:
            if self.state == "running":
                if self.child is None:
                    self._notify(
                        "openjev", "Cannot stop", "Server was started outside the menu bar."
                    )
                    return
                terminate_server(self.child)
                self.child = None
                self._set_state("stopped")
                return
            self.child = spawn_server(fake=self.fake)
            self._set_state("starting")

        def on_open(self, _sender: object) -> None:
            webbrowser.open(self.base_url + "/ui/")

        def on_add_model(self, _sender: object) -> None:
            response = rumps.Window(
                title="Add model",
                message="Hugging Face repo id (org/name), e.g. cross-encoder/nli-deberta-v3-small",
                default_text="cross-encoder/nli-deberta-v3-small",
            ).run()
            if not response.clicked or not response.text.strip():
                return
            repo = response.text.strip()
            entry = ModelEntry(id=repo.split("/")[-1].lower(), repo=repo)
            try:
                Registry.load().add(entry)
            except ValueError as exc:
                self._notify("openjev", "Add failed", str(exc))
                return
            self._config_seen = -1.0
            self._notify(
                "openjev",
                "Model added",
                f"{entry.id} registered — start the download from the Download menu.",
            )

        def on_download(self, model_id: str) -> None:
            """Start an in-process observable download on a daemon thread."""
            from openjev_tool.downloader import DownloadTracker

            if self.download_thread is not None and self.download_thread.is_alive():
                self._notify("openjev", "Download busy", "Another download is already running.")
                return
            entry = next((item for item in load_config().models if item.id == model_id), None)
            if entry is None:
                return
            self.download_id = model_id
            self.download_error = None
            self.download_tracker = DownloadTracker(model_id)
            self.progress_item.title = f"Downloading {model_id}…"
            self.download_thread = threading.Thread(
                target=self._run_download, args=(entry,), daemon=True
            )
            self.download_thread.start()
            self._rebuild_menu()
            self._notify(
                "openjev",
                "Download started",
                f"{entry.repo} — progress shows in the menu bar.",
            )

        def _run_download(self, entry: ModelEntry) -> None:
            """Worker: download with progress; record any failure."""
            from openjev_tool.downloader import download_with_progress

            try:
                download_with_progress(entry, tracker=self.download_tracker)
                self.download_error = None
            except Exception as exc:
                self.download_error = str(exc)

        def _tick_progress(self, _sender: object | None = None) -> None:
            """1s timer: refresh title bar and progress row; finish when done."""
            self._tick_download()

        def _tick_download(self) -> None:
            """Publish the live snapshot; on completion notify and rebuild the menu."""
            thread = self.download_thread
            if thread is None or self.download_tracker is None:
                return
            snapshot = self.download_tracker.snapshot()
            if thread.is_alive():
                self.title = render_title(snapshot)
                if self.progress_row is not None:
                    self.progress_row.update(snapshot)
                else:
                    self.progress_item.title = render_lines(snapshot)[0]
                return
            self.download_thread = None
            failed = self.download_error is not None
            detail = self.download_error or self.download_id
            self._config_seen = -1.0
            self.title = state_title(self.state)
            self._rebuild_menu()
            self._notify(
                "openjev",
                "Download failed" if failed else "Download complete",
                str(detail),
            )

        def on_model_toggle(self, sender: object) -> None:
            model_id = str(getattr(sender, "title", "")).split(" · ")[0]
            enabled = bool(getattr(sender, "state", False))
            registry = Registry.load()
            try:
                registry.set_enabled(model_id, enabled)
            except ValueError:
                return
            if self.state == "running":
                reload_running_server(self.base_url)

        def on_tick(self, _sender: object) -> None:
            mtime = config_mtime()
            if mtime != self._config_seen:
                self._config_seen = mtime
                self._rebuild_menu()
            health = fetch_json(self.base_url, "/health")
            if health is not None:
                self._set_state("running")
                self.stats_item.title = format_stats_line(fetch_json(self.base_url, "/stats"))
                return
            if self.child is not None:
                exited = self.child.poll()
                if exited is not None:
                    self.child = None
                    self._set_state("error")
                else:
                    self._set_state("starting")
                return
            self._set_state("stopped")
            self.stats_item.title = "server offline"

    OpenJevMenuBar().run()
