"""Tests for menu bar pure helpers (no rumps import, no GUI)."""

from __future__ import annotations

import subprocess
from typing import Any

from openjev_tool.config import Config, ModelEntry
from openjev_tool.menubar import (
    config_mtime,
    fetch_json,
    format_bytes,
    format_model_title,
    format_stats_line,
    reload_running_server,
    repo_cache_bytes,
    server_url,
    spawn_server,
    state_title,
    terminate_process,
    terminate_server,
)


def test_server_url_uses_config() -> None:
    config = Config()
    config.server.host = "0.0.0.0"
    config.server.port = 9999
    assert server_url(config) == "http://0.0.0.0:9999"


def test_fetch_json_offline_returns_none() -> None:
    assert fetch_json("http://127.0.0.1:1", "/health") is None


def test_reload_running_server_offline_returns_false() -> None:
    assert reload_running_server("http://127.0.0.1:1") is False


def test_format_stats_line_variants() -> None:
    assert format_stats_line(None) == "server offline"
    stats = {
        "totals": {"invocations": 1284, "errors": 2},
        "endpoints": {"score": {"p50_ms": 84.0}},
    }
    line = format_stats_line(stats)
    assert "1,284 invocations" in line
    assert "2 errors" in line
    assert "p50 84ms" in line
    empty = format_stats_line({"totals": {}, "endpoints": {}})
    assert "0 invocations" in empty


def test_state_title_mapping() -> None:
    assert state_title("stopped") == "○ jev"
    assert state_title("starting") == "◐ jev"
    assert state_title("running") == "● jev"
    assert state_title("error") == "✕ jev"
    try:
        state_title("bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_format_model_title_cache_marker() -> None:
    entry = ModelEntry(id="model-a", repo="org/a")
    assert format_model_title(entry, True) == "model-a · cached"
    assert format_model_title(entry, False) == "model-a · not cached"


def test_format_bytes() -> None:
    assert format_bytes(0) == "0 MB"
    assert format_bytes(142 * 1_000_000) == "142 MB"
    assert format_bytes(8_100_000_000) == "8.1 GB"


def test_repo_cache_bytes_failure_returns_zero(monkeypatch: Any) -> None:
    import huggingface_hub

    monkeypatch.setattr(
        huggingface_hub, "scan_cache_dir", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    assert repo_cache_bytes("org/missing") == 0


def test_config_mtime_zero_when_missing(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENJEV_CONFIG", "/nonexistent/openjev/config.json")
    assert config_mtime() == 0.0


class _FakeProcess:
    """Popen stand-in for terminate tests."""

    def __init__(self, exited: bool = False) -> None:
        self.exited = exited
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return 0 if self.exited else None

    def terminate(self) -> None:
        self.terminated = True
        self.exited = True

    def kill(self) -> None:
        self.killed = True
        self.exited = True

    def wait(self, timeout: float = 0) -> int:
        return 0


def test_terminate_process_noop_cases() -> None:
    terminate_process(None)
    terminate_process(_FakeProcess(exited=True))  # type: ignore[arg-type]
    terminate_server(None)


def test_terminate_process_terminates_running_child() -> None:
    process = _FakeProcess()
    terminate_process(process)  # type: ignore[arg-type]
    assert process.terminated is True
    assert process.killed is False


def test_spawn_server_argv(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Popen:
        def __init__(self, args: list[str], **kwargs: object) -> None:
            captured["args"] = args

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    process = spawn_server(fake=True)
    assert process is not None
    assert captured["args"] == [
        captured["args"][0],
        "-m",
        "openjev_tool.cli",
        "serve",
        "--supervised",
        "--fake",
    ]
