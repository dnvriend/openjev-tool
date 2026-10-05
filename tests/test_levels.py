"""Tests for the 10-level ladder table."""

from __future__ import annotations

import json

import pytest

from openjev_tool.levels import LEVELS, level_by_number, levels_payload


def test_ladder_has_ten_unique_levels() -> None:
    numbers = [level.number for level in LEVELS]
    names = [level.name for level in LEVELS]
    assert numbers == list(range(1, 11))
    assert len(set(names)) == 10


@pytest.mark.parametrize("level", LEVELS, ids=lambda level: f"{level.number}-{level.name}")
def test_every_level_has_complete_example(level) -> None:
    assert level.endpoint in ("score", "noul", "choice", "rerank", "grade", "ask")
    assert level.example
    json.dumps(level.example)


def test_level_by_number_lookup_and_error() -> None:
    assert level_by_number(1).name == "Basic Decision"
    assert level_by_number(10).endpoint == "ask"
    with pytest.raises(ValueError, match="unknown level 11"):
        level_by_number(11)


def test_levels_payload_shape() -> None:
    payload = levels_payload()
    assert len(payload) == 10
    first = payload[0]
    assert first["number"] == 1
    assert {"name", "endpoint", "description", "example"} <= set(first)
