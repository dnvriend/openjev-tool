"""Tests for backend helpers: device selection, fake probs, permutation."""

from __future__ import annotations

from openjev_tool.backend import (
    CANONICAL_LABELS,
    FakeBackend,
    TorchNliBackend,
    fake_label_probs,
    loaded_model_count,
    make_backend,
    select_device,
    to_canonical,
    unload_all,
)
from openjev_tool.config import ModelEntry


def test_select_device_explicit_wins() -> None:
    assert select_device("cpu") == "cpu"
    assert select_device(None) in ("mps", "cuda", "cpu")


def test_to_canonical_identity() -> None:
    probs = [0.1, 0.7, 0.2]
    assert to_canonical(probs, CANONICAL_LABELS) == probs


def test_to_canonical_permuted() -> None:
    swapped = ("contradiction", "neutral", "entailment")
    probs = [0.1, 0.7, 0.2]
    assert to_canonical(probs, swapped) == [0.1, 0.2, 0.7]


def test_fake_label_probs_deterministic_and_directional() -> None:
    same = fake_label_probs("a man plays guitar", "a man plays guitar")
    assert same["entailment"] > same["contradiction"]
    disjoint = fake_label_probs("the sky is blue", "my code compiles")
    assert disjoint["contradiction"] > disjoint["entailment"]
    assert fake_label_probs("x y", "x y") == fake_label_probs("x y", "x y")


def test_fake_backend_canonicalizes_label_order() -> None:
    entry = ModelEntry(
        id="fake",
        repo="org/fake",
        labels=("contradiction", "neutral", "entailment"),
    )
    backend = FakeBackend(entry)
    assert backend.id == "fake"
    assert backend.is_loaded() is True
    rows = backend.predict_probs([("a man plays guitar", "a man plays guitar")])
    entail = rows[0][CANONICAL_LABELS.index("entailment")]
    contradict = rows[0][CANONICAL_LABELS.index("contradiction")]
    assert entail > contradict


def test_make_backend_fake_and_env(monkeypatch) -> None:
    entry = ModelEntry(id="fake", repo="org/fake")
    assert isinstance(make_backend(entry, "cpu", fake=True), FakeBackend)
    monkeypatch.setenv("OPENJEV_FAKE", "1")
    assert isinstance(make_backend(entry, "cpu", fake=False), FakeBackend)
    monkeypatch.delenv("OPENJEV_FAKE")
    assert isinstance(make_backend(entry, "cpu", fake=False), TorchNliBackend)


def test_torch_backend_cache_state_reflects_load_unload() -> None:
    entry = ModelEntry(id="t", repo="org/t")
    backend = TorchNliBackend(entry, "cpu")
    assert backend.id == "t"
    assert backend.capabilities == entry.capabilities
    assert backend.is_loaded() is False
    unload_all()
    assert loaded_model_count() == 0
