"""Tests for the centralized logging configuration (pure, no network)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from openjev_tool.logging_config import get_logger, setup_logging


def _clear_handlers() -> None:
    logging.getLogger().handlers.clear()


def test_setup_logging_warning_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_FILE", raising=False)
    setup_logging(0)
    assert logging.getLogger().level == logging.WARNING
    _clear_handlers()


def test_setup_logging_info_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_FILE", raising=False)
    setup_logging(1)
    assert logging.getLogger().level == logging.INFO
    _clear_handlers()


def test_setup_logging_debug_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_FILE", raising=False)
    setup_logging(2)
    assert logging.getLogger().level == logging.DEBUG
    _clear_handlers()


def test_setup_logging_file_handler(tmp_path: Path) -> None:
    log_file = tmp_path / "app.log"
    setup_logging(1, log_file=str(log_file))
    get_logger("test").info("hello")
    for h in logging.getLogger().handlers:
        h.flush()
    assert log_file.exists()
    _clear_handlers()
