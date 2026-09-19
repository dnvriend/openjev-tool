"""Centralized logging configuration with multi-level verbosity support.

This module provides setup_logging() for configuring logging based on
verbosity count from CLI arguments (-v, -vv, -vvv).

Supports both stderr and file logging via environment variables:
    LOG_FILE: Path to log file (optional, disables stderr when set)
    LOG_FORMAT: Log format string (optional)

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Default log format
DEFAULT_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
CONSOLE_LOG_FORMAT = "[%(levelname)s] %(message)s"


def setup_logging(
    verbose_count: int = 0,
    log_file: str | None = None,
    log_format: str | None = None,
) -> None:
    """Configure logging based on verbosity level.

    Maps CLI verbosity count to Python logging levels and configures
    both application and dependent library loggers.

    Args:
        verbose_count: Number of -v flags (0-3+)
            0: WARNING level (quiet mode)
            1: INFO level (normal verbose)
            2: DEBUG level (detailed debugging)
            3+: DEBUG + enable dependent library logging (trace mode)
        log_file: Path to log file. If not provided, checks LOG_FILE env var.
            When set, logs go to file instead of stderr.
        log_format: Custom log format. If not provided, checks LOG_FORMAT env var.

    Environment Variables:
        LOG_FILE: Path to log file (rotated at 10MB, keeps 5 backups)
        LOG_FORMAT: Custom log format string

    Example:
        >>> setup_logging(0)  # No -v flag: WARNING only, logs to stderr
        >>> setup_logging(1)  # -v: INFO level
        >>> setup_logging(2, log_file="/var/log/app.log")  # DEBUG to file
        >>> # Or via environment:
        >>> # LOG_FILE=/var/log/app.log python app.py
    """
    # Map verbosity count to logging levels
    if verbose_count == 0:
        level = logging.WARNING
    elif verbose_count == 1:
        level = logging.INFO
    elif verbose_count >= 2:
        level = logging.DEBUG
    else:
        level = logging.WARNING

    # Get log file from argument or environment
    file_path = log_file or os.environ.get("LOG_FILE")
    fmt = log_format or os.environ.get("LOG_FORMAT")

    # Get root logger and clear existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    if file_path:
        # File logging with rotation
        _setup_file_handler(root_logger, file_path, level, fmt)
    else:
        # Console logging to stderr
        _setup_console_handler(root_logger, level, fmt)

    # Configure dependent library loggers at TRACE level (-vvv)
    # Add your project-specific library loggers here
    # Example:
    #   if verbose_count >= 3:
    #       logging.getLogger("requests").setLevel(logging.DEBUG)
    #       logging.getLogger("urllib3").setLevel(logging.DEBUG)


def _setup_console_handler(
    logger: logging.Logger,
    level: int,
    fmt: str | None = None,
) -> None:
    """Set up console handler for stderr output.

    Args:
        logger: Logger to configure.
        level: Logging level.
        fmt: Optional custom format string.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    formatter = logging.Formatter(fmt or CONSOLE_LOG_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _setup_file_handler(
    logger: logging.Logger,
    file_path: str,
    level: int,
    fmt: str | None = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> None:
    """Set up rotating file handler.

    Args:
        logger: Logger to configure.
        file_path: Path to log file.
        level: Logging level.
        fmt: Optional custom format string.
        max_bytes: Maximum file size before rotation (default 10MB).
        backup_count: Number of backup files to keep (default 5).
    """
    # Ensure parent directory exists
    log_path = Path(file_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    formatter = logging.Formatter(
        fmt or DEFAULT_LOG_FORMAT,
        datefmt=DEFAULT_DATE_FORMAT,
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for a module.

    This is a convenience wrapper around logging.getLogger() that
    ensures consistent logger naming across your application.

    Args:
        name: Module name (typically __name__)

    Returns:
        Logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Operation started")
        >>> logger.debug("Detailed operation info")
    """
    return logging.getLogger(name)
