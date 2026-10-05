"""Jev-style NLI scoring commands backed by the openjev cross-encoder.

Wraps any registered NLI cross-encoder (default: ``AlexWortega/openjev``, a
Qwen3.5-4B fine-tune into a 3-way NLI cross-encoder: contradiction /
entailment / neutral) for local inference on Apple Silicon (MPS), CUDA, or
CPU. The registry lives in ``~/.config/openjev/config.json``; model inference
goes through :mod:`openjev_tool.backend`. All results are emitted to stdout as
a single JSON string; diagnostics go to stderr.

Note: This code was generated with assistance from AI coding tools
and has been reviewed and tested by a human.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

import typer

from openjev_tool.app import make_app
from openjev_tool.backend import (
    JevPair,
    ModelBackend,
)
from openjev_tool.backend import (
    select_device as _select_device,
)
from openjev_tool.config import (
    CANONICAL_LABELS,
    DEFAULT_ENTRY_ID,
    DEFAULT_REPO,
    DEFAULT_REVISION,
    DEFAULT_SUBFOLDER,
    ModelEntry,
    load_config,
)
from openjev_tool.output import emit_json as _emit
from openjev_tool.output import fail as _fail
from openjev_tool.registry import download_entry

jev_app = make_app("Score text with a registered NLI cross-encoder (local).")

LABELS: tuple[str, ...] = CANONICAL_LABELS
DEFAULT_MODEL_REPO = DEFAULT_REPO
MODEL_SUBFOLDER = DEFAULT_SUBFOLDER
DEFAULT_DEVICE = "mps"


def _model_repo() -> str:
    """Return the Hugging Face repo id, overridable via OPENJEV_MODEL."""
    return os.environ.get("OPENJEV_MODEL", DEFAULT_REPO)


def _model_revision() -> str:
    """Return the pinned HF revision, overridable via OPENJEV_REVISION."""
    return os.environ.get("OPENJEV_REVISION", DEFAULT_REVISION)


def _resolve_entry() -> ModelEntry:
    """Resolve the ModelEntry used for direct CLI inference.

    Precedence: OPENJEV_MODEL env (ad-hoc entry, v1 behavior) > the config
    entry with id ``openjev-qwen3.5-4b`` > the built-in default entry.
    OPENJEV_REVISION overrides the revision of whichever entry wins.
    """
    env_repo = os.environ.get("OPENJEV_MODEL")
    if env_repo:
        entry = ModelEntry(id="env-model", repo=env_repo, subfolder=MODEL_SUBFOLDER)
    else:
        config = load_config()
        entry = next(
            (item for item in config.models if item.id == DEFAULT_ENTRY_ID),
            ModelEntry(
                id=DEFAULT_ENTRY_ID,
                repo=DEFAULT_REPO,
                subfolder=DEFAULT_SUBFOLDER,
                revision=DEFAULT_REVISION,
            ),
        )
    env_revision = os.environ.get("OPENJEV_REVISION")
    if env_revision:
        entry.revision = env_revision
    return entry


def _make_backend(entry: ModelEntry, device: str) -> ModelBackend:
    """Build the backend for ``entry`` (fake when OPENJEV_FAKE=1)."""
    from openjev_tool.backend import make_backend

    return make_backend(entry, device, fake=False)


def _build_rerank_result(question: str, options: list[str], scores: list[float]) -> dict[str, Any]:
    """Pure mapping of per-option entailment scores onto the rerank JSON result."""
    best = max(range(len(options)), key=lambda i: scores[i])
    return {
        "question": question,
        "options": options,
        "best_index": best,
        "best_option": options[best],
        "entailment_scores": {
            option: round(score, 6) for option, score in zip(options, scores, strict=True)
        },
    }


def _build_grade_result(
    question: str, reference: str, candidate: str, label: str
) -> dict[str, Any]:
    """Pure mapping onto the grade command's JSON result."""
    return {
        "question": question,
        "reference": reference,
        "candidate": candidate,
        "label": label,
    }


def _option_entailment(backend: ModelBackend, question: str, options: list[str]) -> list[float]:
    """P(entailment) of "The correct answer is: {option}" for each option."""
    entail_idx = LABELS.index("entailment")
    pairs: list[JevPair] = [(question, f"The correct answer is: {option}") for option in options]
    probs = backend.predict_probs(pairs)
    return [row[entail_idx] for row in probs]


def _build_choice_result(question: str, options: list[str], scores: list[float]) -> dict[str, Any]:
    """Pure mapping onto the choice command's JSON result (Jev-Choice shape).

    ``probabilities`` is the raw entailment evidence normalized into a
    distribution over options; ``entailment_scores`` keeps the raw values.
    """
    best = max(range(len(options)), key=lambda i: scores[i])
    total = sum(scores)
    probs = [s / total if total > 0 else 1 / len(options) for s in scores]
    return {
        "question": question,
        "options": options,
        "choice": options[best],
        "index": best,
        "probabilities": {option: round(p, 6) for option, p in zip(options, probs, strict=True)},
        "entailment_scores": {
            option: round(score, 6) for option, score in zip(options, scores, strict=True)
        },
    }


def _build_noul_result(premise: str, claim: str, probs: list[float]) -> dict[str, Any]:
    """Pure mapping onto the noul command's JSON result (Jev-Noul shape).

    ``verdict`` maps the argmax label onto true / false / unknown; ``holds``
    is that as a tri-state boolean; ``confidence`` is the probability of the
    deciding class (P(true) when true, P(false) when false, P(unknown) when
    the model cannot judge — the "noul" case).
    """
    verdict_by_label = {"entailment": "true", "contradiction": "false", "neutral": "unknown"}
    confidence_by_verdict = {"true": "entailment", "false": "contradiction", "unknown": "neutral"}
    best = max(range(len(probs)), key=lambda i: probs[i])
    verdict = verdict_by_label[LABELS[best]]
    holds = {"true": True, "false": False, "unknown": None}[verdict]
    return {
        "premise": premise,
        "claim": claim,
        "holds": holds,
        "confidence": round(probs[LABELS.index(confidence_by_verdict[verdict])], 6),
        "verdict": verdict,
        "probabilities": {label: round(p, 6) for label, p in zip(LABELS, probs, strict=True)},
    }


def _build_judge_score_result(premise: str, hypothesis: str, probs: list[float]) -> dict[str, Any]:
    """Pure mapping onto the score command's JSON result (poor-man's Jev Score).

    ``score`` is P(entailment) - P(contradiction) on a [-1, 1] scale; neutral
    probability shrinks it toward 0 (unjudged), it never pushes it to extremes.
    """
    entail = probs[LABELS.index("entailment")]
    contradict = probs[LABELS.index("contradiction")]
    best = max(range(len(probs)), key=lambda i: probs[i])
    return {
        "premise": premise,
        "hypothesis": hypothesis,
        "score": round(entail - contradict, 6),
        "scale": [-1, 1],
        "label": LABELS[best],
        "probabilities": {label: round(p, 6) for label, p in zip(LABELS, probs, strict=True)},
    }


def _post_json(server: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``payload`` as JSON to ``server``/``path``; return the decoded response."""
    url = server.rstrip("/") + path
    if not url.startswith(("http://", "https://")):
        _fail(f"server URL must be http(s), got: {url}")
    req = urllib_request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=600) as resp:  # nosec B310 — scheme checked above
            return dict(json.loads(resp.read().decode()))
    except HTTPError as exc:
        _fail(f"server error {exc.code} for {url}: {exc.read().decode(errors='replace')}")
    except (URLError, OSError) as exc:
        _fail(f"request to {url} failed: {exc} — is the server running? (openjev-tool serve)")
    raise AssertionError("unreachable")  # for type checkers; _fail always exits


@jev_app.command(name="download")
def download(
    device: Annotated[
        str,
        typer.Option(
            "--device", envvar="OPENJEV_DEVICE", help="Ignored for download; kept for symmetry."
        ),
    ] = DEFAULT_DEVICE,
) -> None:
    """Download the openjev model weights into the local Hugging Face cache.

    \b
    Examples:
      # Fetch the qwen3.5-4b-nli checkpoint (~8 GB, cached under ~/.cache/huggingface)
      openjev-tool jev download

      # Use a different model repo
      OPENJEV_MODEL=myorg/my-jev openjev-tool jev download

    The score/rerank/grade commands reuse this cache and never re-download.
    """
    entry = _resolve_entry()
    try:
        path = download_entry(entry)
        _emit({"id": entry.id, "repo": entry.repo, "subfolder": entry.subfolder, "path": path})
    except Exception as exc:  # pragma: no cover - network failure path
        _fail(f"download failed: {exc}")


@jev_app.command(name="noul")
def noul(
    premise: Annotated[str, typer.Argument(help="The premise (context) text.")],
    claim: Annotated[str, typer.Argument(help="The claim to check against the premise.")],
    device: Annotated[
        str,
        typer.Option("--device", envvar="OPENJEV_DEVICE", help="Torch device (mps, cuda, cpu)."),
    ] = "",
    server: Annotated[
        str,
        typer.Option(
            "--server",
            envvar="OPENJEV_SERVER",
            help="Check via a running serve instance instead of loading locally.",
        ),
    ] = "",
) -> None:
    """Jev-Noul: check a claim against context; verdict true/false/unknown as JSON.

    \b
    Examples:
      openjev-tool jev noul "A man is playing a guitar." "Someone is making music."

      openjev-tool jev noul "The refund policy is 30 days." "The customer bought a TV."  # unknown

      openjev-tool jev noul --server http://127.0.0.1:8080 "The sky is blue." "The sky is green."

    Output is a JSON object: {"premise", "claim", "holds" (true/false/null),
    "confidence", "verdict" (true/false/unknown), "probabilities"}. A "null"
    holds (unknown) is the noul case: the premise does not settle the claim.
    """
    if server:
        _emit(_post_json(server, "/noul", {"premise": premise, "claim": claim}))
        return
    try:
        backend = _make_backend(_resolve_entry(), _select_device(device or None))
        probs = backend.predict_probs([(premise, claim)])
        _emit(_build_noul_result(premise, claim, probs[0]))
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(f"noul failed: {exc}")


@jev_app.command(name="score")
def score(
    premise: Annotated[str, typer.Argument(help="The premise (context) text.")],
    hypothesis: Annotated[str, typer.Argument(help="The statement to score against the premise.")],
    device: Annotated[
        str,
        typer.Option("--device", envvar="OPENJEV_DEVICE", help="Torch device (mps, cuda, cpu)."),
    ] = "",
    server: Annotated[
        str,
        typer.Option(
            "--server",
            envvar="OPENJEV_SERVER",
            help="Score via a running serve instance instead of loading locally.",
        ),
    ] = "",
) -> None:
    """Jev-Score: continuous judgment of a statement on a [-1, 1] scale as JSON.

    \b
    Examples:
      openjev-tool jev score "A man is playing a guitar." "Someone is making music."

      openjev-tool jev score "The PR adds a retry loop." "The PR fixes flaky tests."

      openjev-tool jev score --server http://127.0.0.1:8080 "The sky is blue." "The sky is green."

    Output is a JSON object: {"premise", "hypothesis", "score" (P(entailment) -
    P(contradiction), in [-1, 1]), "scale", "label", "probabilities"}. Note:
    unlike TypeSafe Jev's trained calibration, this score is derived from NLI
    probabilities — treat it as ordinal, not absolutely calibrated.
    """
    if server:
        _emit(_post_json(server, "/score", {"premise": premise, "hypothesis": hypothesis}))
        return
    try:
        backend = _make_backend(_resolve_entry(), _select_device(device or None))
        probs = backend.predict_probs([(premise, hypothesis)])
        _emit(_build_judge_score_result(premise, hypothesis, probs[0]))
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(f"score failed: {exc}")


@jev_app.command(name="choice")
def choice(
    question: Annotated[str, typer.Argument(help="The question (used as the premise).")],
    options: Annotated[
        list[str], typer.Argument(help="Candidate options; highest P(entailment) wins.")
    ],
    device: Annotated[
        str,
        typer.Option("--device", envvar="OPENJEV_DEVICE", help="Torch device (mps, cuda, cpu)."),
    ] = "",
    server: Annotated[
        str,
        typer.Option(
            "--server",
            envvar="OPENJEV_SERVER",
            help="Choose via a running serve instance instead of loading locally.",
        ),
    ] = "",
) -> None:
    """Jev-Choice: pick from runtime options; returns a distribution as JSON.

    \b
    Examples:
      openjev-tool jev choice "What is the capital of France?" Paris Lyon Berlin

      openjev-tool jev choice "Should we refund?" yes no

      openjev-tool jev choice --server http://127.0.0.1:8080 "Which language is typed?" Python Dutch

    Output is a JSON object: {"question", "options", "choice", "index",
    "probabilities" (normalized distribution over options), "entailment_scores"
    (raw P(entailment) per option)}. Note: unlike TypeSafe Jev's single-pass
    schema readout, this scores each option in its own forward pass.
    """
    if not options:
        _fail("choice requires at least one option")
    if server:
        _emit(_post_json(server, "/choice", {"question": question, "options": list(options)}))
        return
    try:
        backend = _make_backend(_resolve_entry(), _select_device(device or None))
        scores = _option_entailment(backend, question, list(options))
        _emit(_build_choice_result(question, list(options), scores))
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(f"choice failed: {exc}")


@jev_app.command(name="rerank")
def rerank(
    question: Annotated[str, typer.Argument(help="The question (used as the premise).")],
    options: Annotated[
        list[str], typer.Argument(help="Candidate options; highest P(entailment) wins.")
    ],
    device: Annotated[
        str,
        typer.Option("--device", envvar="OPENJEV_DEVICE", help="Torch device (mps, cuda, cpu)."),
    ] = "",
    server: Annotated[
        str,
        typer.Option(
            "--server",
            envvar="OPENJEV_SERVER",
            help="Rerank via a running serve instance instead of loading locally.",
        ),
    ] = "",
) -> None:
    """Zero-shot multiple choice: pick the option best entailed by the question.

    \b
    Examples:
      openjev-tool jev rerank "What is the capital of France?" Paris Lyon Berlin

      openjev-tool jev rerank "Which language is typed?" Python Dutch

      # Against a running 'openjev-tool serve' (fast: model stays in memory)
      openjev-tool jev rerank --server http://127.0.0.1:8080 "Which language is typed?" Python Dutch

    Output is a JSON object: {"question", "options", "best_index", "best_option",
    "entailment_scores"}.
    """
    if not options:
        _fail("rerank requires at least one option")
    if server:
        _emit(_post_json(server, "/rerank", {"question": question, "options": list(options)}))
        return
    try:
        backend = _make_backend(_resolve_entry(), _select_device(device or None))
        scores = _option_entailment(backend, question, list(options))
        _emit(_build_rerank_result(question, list(options), scores))
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(f"rerank failed: {exc}")


@jev_app.command(name="grade")
def grade(
    question: Annotated[str, typer.Argument(help="The question that was asked.")],
    reference: Annotated[str, typer.Argument(help="The reference (gold) answer.")],
    candidate: Annotated[str, typer.Argument(help="The candidate answer to grade.")],
    device: Annotated[
        str,
        typer.Option("--device", envvar="OPENJEV_DEVICE", help="Torch device (mps, cuda, cpu)."),
    ] = "",
    server: Annotated[
        str,
        typer.Option(
            "--server",
            envvar="OPENJEV_SERVER",
            help="Grade via a running serve instance instead of loading locally.",
        ),
    ] = "",
) -> None:
    """Grade a candidate answer against a reference; print the verdict as JSON.

    \b
    Examples:
      openjev-tool jev grade "What is 2+2?" "4" "four"

      openjev-tool jev grade "Capital of France?" "Paris" "London"

      # Against a running 'openjev-tool serve' (fast: model stays in memory)
      openjev-tool jev grade --server http://127.0.0.1:8080 "What is 2+2?" "4" "four"

    Output is a JSON object: {"question", "reference", "candidate", "label"} where
    label is entailment (correct), contradiction (wrong), or neutral.
    """
    if server:
        _emit(
            _post_json(
                server,
                "/grade",
                {"question": question, "reference": reference, "candidate": candidate},
            )
        )
        return
    try:
        backend = _make_backend(_resolve_entry(), _select_device(device or None))
        probs = backend.predict_probs(
            [(f"{question}\nReference answer: {reference}", f"Answer: {candidate}")]
        )
        label = LABELS[max(range(len(probs[0])), key=lambda i: probs[0][i])]
        _emit(_build_grade_result(question, reference, candidate, label))
    except typer.Exit:
        raise
    except Exception as exc:
        _fail(f"grade failed: {exc}")


@jev_app.command(name="serve")
def serve(
    host: Annotated[
        str, typer.Option("--host", help="Bind address (default: config server.host).")
    ] = "",
    port: Annotated[
        int, typer.Option("--port", help="Bind port (default: config server.port).")
    ] = 0,
    device: Annotated[
        str,
        typer.Option("--device", envvar="OPENJEV_DEVICE", help="Torch device (mps, cuda, cpu)."),
    ] = "",
) -> None:
    """Start the local judgment server (kept for v1 compatibility).

    \b
    Examples:
      # Prefer the new root command (same server, more options):
      openjev-tool serve

      # Terminal 1: start on the configured port
      openjev-tool jev serve

      # Terminal 2: score against it
      openjev-tool jev score --server http://127.0.0.1:8080 "The sky is blue." "The sky is green."

      # Custom port and device
      openjev-tool jev serve --port 9090 --device cpu

    Serves /v1/score, /v1/noul, /v1/choice, /v1/rerank, /v1/grade, /v1/ask,
    /v1/invoke plus legacy aliases /score ... /grade and GET /health, /stats,
    /v1/models, /v1/levels.
    """
    from openjev_tool.server import run_server

    typer.secho("starting jev server", err=True, fg=typer.colors.GREEN)
    run_server(host=host or None, port=port or None, device=device)
