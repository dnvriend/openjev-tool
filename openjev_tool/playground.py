"""Playground web UI served by the openjev server at /ui.

A single static ``index.html`` (vanilla JS, no build step, no CDN) that
exercises the 10-level ladder against the running server: pick a level, edit
the fixture, invoke, inspect the typed judgment and its routing, replay the
exact curl, toggle models, and watch stats.
"""

from __future__ import annotations

from pathlib import Path


def playground_path() -> Path:
    """Directory holding the static playground assets."""
    return Path(__file__).parent / "static"
