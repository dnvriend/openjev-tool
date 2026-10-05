"""Model backends: the protocol, the torch NLI implementation, and a fake.

A backend owns one :class:`~openjev_tool.config.ModelEntry` and turns
(premise, hypothesis) pairs into label probabilities in canonical order
(``contradiction``, ``entailment``, ``neutral``) so the result builders in
:mod:`openjev_tool.jev` are model-agnostic. Torch imports stay lazy so tests
and the fake path never pay the import or memory cost.
"""

from __future__ import annotations

import os
import re
from typing import Any, Protocol

from openjev_tool.config import CANONICAL_LABELS, ModelEntry

DEFAULT_DEVICE = "mps"

LoadCacheKey = tuple[str, str, str, str]
JevPair = tuple[str, str]

_MODELS: dict[LoadCacheKey, tuple[Any, Any]] = {}


def select_device(requested: str | None) -> str:
    """Pick the torch device: explicit request wins, else MPS > CUDA > CPU."""
    if requested:
        return requested
    import torch

    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def to_canonical(probs: list[float], labels: tuple[str, ...]) -> list[float]:
    """Permute model-order probabilities into canonical label order.

    Args:
        probs: Probabilities in the order the checkpoint emits logits.
        labels: The checkpoint's label order (``ModelEntry.labels``).

    Returns:
        Probabilities ordered as ``CANONICAL_LABELS``.
    """
    return [probs[labels.index(label)] for label in CANONICAL_LABELS]


def _words(text: str) -> set[str]:
    """Lowercased word set of ``text`` (letters/digits/underscore only)."""
    return set(re.findall(r"\w+", text.lower()))


def fake_label_probs(premise: str, hypothesis: str) -> dict[str, float]:
    """Deterministic pseudo-probabilities per label from word overlap.

    Identical texts maximize entailment; disjoint texts maximize contradiction;
    neutral never wins. Used by :class:`FakeBackend` for tests and
    ``OPENJEV_FAKE=1`` development without any model download.
    """
    left = _words(premise)
    right = _words(hypothesis)
    union = left | right
    overlap = len(left & right) / len(union) if union else 1.0
    return {
        "entailment": 0.2 + 0.6 * overlap,
        "contradiction": 0.8 - 0.6 * overlap,
        "neutral": 0.49,
    }


class ModelBackend(Protocol):
    """The inference interface every judgment model implements."""

    @property
    def id(self) -> str:
        """The model entry id."""
        ...

    @property
    def capabilities(self) -> tuple[str, ...]:
        """The endpoints this model may serve."""
        ...

    def load(self) -> None:
        """Make the model ready for inference (may be slow)."""
        ...

    def unload(self) -> None:
        """Release model memory."""
        ...

    def is_loaded(self) -> bool:
        """Return True when the model is resident and ready."""
        ...

    def predict_probs(self, pairs: list[JevPair]) -> list[list[float]]:
        """Score pairs; return probabilities in canonical label order."""
        ...


class FakeBackend:
    """Deterministic word-overlap backend for tests and offline development."""

    def __init__(self, entry: ModelEntry) -> None:
        """Store the entry; nothing is ever loaded from disk."""
        self.entry = entry

    @property
    def id(self) -> str:
        """The model entry id."""
        return self.entry.id

    @property
    def capabilities(self) -> tuple[str, ...]:
        """The model entry capabilities."""
        return self.entry.capabilities

    def load(self) -> None:
        """No-op: the fake backend is always ready."""

    def unload(self) -> None:
        """No-op: the fake backend holds no memory."""

    def is_loaded(self) -> bool:
        """Always True."""
        return True

    def predict_probs(self, pairs: list[JevPair]) -> list[list[float]]:
        """Map each pair through :func:`fake_label_probs` into canonical order."""
        results: list[list[float]] = []
        for premise, hypothesis in pairs:
            per_label = fake_label_probs(premise, hypothesis)
            model_order = [per_label[label] for label in self.entry.labels]
            results.append(to_canonical(model_order, self.entry.labels))
        return results


class TorchNliBackend:
    """NLI cross-encoder backend backed by torch/transformers (MPS/CUDA/CPU)."""

    def __init__(self, entry: ModelEntry, device: str) -> None:
        """Bind the backend to one model entry on one device."""
        self.entry = entry
        self.device = device

    @property
    def id(self) -> str:
        """The model entry id."""
        return self.entry.id

    @property
    def capabilities(self) -> tuple[str, ...]:
        """The model entry capabilities."""
        return self.entry.capabilities

    def _cache_key(self) -> LoadCacheKey:
        return (self.entry.repo, self.entry.subfolder, self.entry.revision, self.device)

    def is_loaded(self) -> bool:
        """True when this (repo, subfolder, revision, device) is resident."""
        return self._cache_key() in _MODELS

    def load(self) -> None:
        """Load (tokenizer, model) into the shared cache on first use."""
        _load_model(self.entry.repo, self.entry.subfolder, self.entry.revision, self.device)

    def unload(self) -> None:
        """Drop the resident model for this cache key."""
        _MODELS.pop(self._cache_key(), None)

    def predict_probs(self, pairs: list[JevPair]) -> list[list[float]]:
        """Run the cross-encoder on pairs; return canonical-order probabilities.

        A checkpoint-declared template (``model.config.nli_template``) wins over
        the configured template, matching v1 behavior for the pinned openjev
        checkpoint.
        """
        import torch

        tokenizer, model = _load_model(
            self.entry.repo, self.entry.subfolder, self.entry.revision, self.device
        )
        template = getattr(model.config, "nli_template", None) or self.entry.template
        texts = [template.format(premise=p.strip(), hypothesis=h.strip()) for p, h in pairs]
        encoded = tokenizer(
            texts, truncation=True, max_length=4096, padding=True, return_tensors="pt"
        )
        with torch.no_grad():
            logits = model(**{k: v.to(self.device) for k, v in encoded.items()}).logits
        probs = torch.softmax(logits.float(), dim=-1)
        rows = [[float(v) for v in row] for row in probs]
        return [to_canonical(row, self.entry.labels) for row in rows]


def make_backend(entry: ModelEntry, device: str, fake: bool = False) -> ModelBackend:
    """Construct a backend for ``entry``; fake=True (or OPENJEV_FAKE=1) avoids torch."""
    if fake or os.environ.get("OPENJEV_FAKE") == "1":
        return FakeBackend(entry)
    return TorchNliBackend(entry, device)


def loaded_model_count() -> int:
    """Number of resident models in the shared cache (for /stats)."""
    return len(_MODELS)


def unload_all() -> None:
    """Drop every resident model (used on server shutdown)."""
    _MODELS.clear()


def _load_model(repo: str, subfolder: str, revision: str, device: str) -> tuple[Any, Any]:
    """Load (tokenizer, model) once per (repo, subfolder, revision, device)."""
    key = (repo, subfolder, revision, device)
    if key not in _MODELS:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()  # type: ignore[no-untyped-call]
        tokenizer = AutoTokenizer.from_pretrained(repo, subfolder=subfolder, revision=revision)
        model = AutoModelForSequenceClassification.from_pretrained(
            repo, subfolder=subfolder, dtype=torch.float16, revision=revision
        )
        model.to(device)
        model.eval()
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        setattr(tokenizer, "padding_side", "right")
        _MODELS[key] = (tokenizer, model)
    return _MODELS[key]
