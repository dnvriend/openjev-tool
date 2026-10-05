"""Tests for the openjev jev command helpers (pure logic only, no model)."""

from __future__ import annotations

import json

import pytest
import typer

from openjev_tool.jev import (
    LABELS,
    MODEL_SUBFOLDER,
    _build_choice_result,
    _build_grade_result,
    _build_judge_score_result,
    _build_noul_result,
    _build_rerank_result,
    _model_repo,
    _select_device,
)


def test_labels_order() -> None:
    assert LABELS == ("contradiction", "entailment", "neutral")


def test_model_subfolder() -> None:
    assert MODEL_SUBFOLDER == "qwen3.5-4b-nli"


def test_select_device_explicit_wins() -> None:
    assert _select_device("cpu") == "cpu"


def test_build_noul_result_true() -> None:
    result = _build_noul_result("p", "claim", [0.1, 0.7, 0.2])
    assert result["verdict"] == "true"
    assert result["holds"] is True
    assert result["confidence"] == 0.7
    json.dumps(result)


def test_build_noul_result_unknown_is_null_holds() -> None:
    result = _build_noul_result("p", "claim", [0.1, 0.2, 0.7])
    assert result["verdict"] == "unknown"
    assert result["holds"] is None
    assert result["confidence"] == 0.7
    json.dumps(result)


def test_build_noul_result_false() -> None:
    result = _build_noul_result("p", "claim", [0.9, 0.05, 0.05])
    assert result["verdict"] == "false"
    assert result["holds"] is False
    assert result["confidence"] == 0.9


def test_build_judge_score_result_in_scale_range() -> None:
    result = _build_judge_score_result("p", "h", [0.1, 0.7, 0.2])
    assert result["score"] == pytest.approx(0.6)
    assert result["scale"] == [-1, 1]
    assert result["label"] == "entailment"
    json.dumps(result)


def test_build_judge_score_result_negative() -> None:
    result = _build_judge_score_result("p", "h", [0.8, 0.1, 0.1])
    assert result["score"] == pytest.approx(-0.7)
    assert result["label"] == "contradiction"


def test_build_choice_result_normalizes_to_distribution() -> None:
    result = _build_choice_result("q?", ["a", "b"], [0.3, 0.1])
    assert result["choice"] == "a"
    assert result["index"] == 0
    assert sum(result["probabilities"].values()) == pytest.approx(1.0)
    assert result["probabilities"]["a"] == pytest.approx(0.75)
    assert result["entailment_scores"] == {"a": 0.3, "b": 0.1}
    json.dumps(result)


def test_build_rerank_result_picks_best_option() -> None:
    result = _build_rerank_result("q?", ["a", "b", "c"], [0.2, 0.9, 0.4])
    assert result["best_index"] == 1
    assert result["best_option"] == "b"
    assert result["entailment_scores"]["b"] == 0.9
    json.dumps(result)


def test_build_grade_result() -> None:
    result = _build_grade_result("q", "ref", "cand", "contradiction")
    assert result == {
        "question": "q",
        "reference": "ref",
        "candidate": "cand",
        "label": "contradiction",
    }
    json.dumps(result)


def test_model_repo_env_override(monkeypatch) -> None:
    from openjev_tool.jev import DEFAULT_MODEL_REPO

    monkeypatch.delenv("OPENJEV_MODEL", raising=False)
    assert _model_repo() == DEFAULT_MODEL_REPO
    monkeypatch.setenv("OPENJEV_MODEL", "other/repo")
    assert _model_repo() == "other/repo"


def test_post_json_rejects_non_http_scheme(capsys) -> None:
    from openjev_tool.jev import _post_json

    with pytest.raises(typer.Exit):
        _post_json("ftp://example.com", "/score", {"premise": "p", "hypothesis": "h"})
    assert "must be http(s)" in capsys.readouterr().err


def test_post_json_connection_error(capsys) -> None:
    from openjev_tool.jev import _post_json

    with pytest.raises(typer.Exit):
        _post_json("http://127.0.0.1:1", "/score", {"premise": "p", "hypothesis": "h"})
    assert "is the server running" in capsys.readouterr().err
