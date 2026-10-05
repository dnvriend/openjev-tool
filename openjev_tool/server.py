"""The openjev v2 server: /v1 judgment API, stats, routing, batch, levels.

``build_app`` produces a FastAPI application that routes every judgment
request through the deterministic router, records stats per endpoint and
model, and stamps ``{"routing": {"model", "reason", "candidates"}}`` onto
every response. Legacy v1 paths (``/score``, ``/noul``, ...) are registered
as aliases of their ``/v1`` counterparts so existing clients keep working.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse

from openjev_tool.backend import ModelBackend, loaded_model_count, select_device
from openjev_tool.config import Config, load_config, save_config
from openjev_tool.jev import (
    _build_choice_result,
    _build_grade_result,
    _build_judge_score_result,
    _build_noul_result,
    _build_rerank_result,
    _option_entailment,
)
from openjev_tool.levels import LEVELS, level_by_number, levels_payload
from openjev_tool.playground import playground_path
from openjev_tool.router import RouterError, RoutingDecision, route
from openjev_tool.stats import StatsCollector, stats_path


def server_version() -> str:
    """The installed openjev-tool package version."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("openjev-tool")
    except PackageNotFoundError:
        return "0.0.0"


@dataclass
class ServerState:
    """Mutable server context shared by all route handlers."""

    config: Config
    device: str
    fake: bool
    stats: StatsCollector
    backends: dict[str, ModelBackend] = field(default_factory=dict)


def _need(payload: Mapping[str, Any], key: str) -> Any:
    """Return ``payload[key]`` or raise ValueError for a 422."""
    value = payload.get(key)
    if value is None or value == "" or value == []:
        raise ValueError(f"missing field: '{key}'")
    return value


def _run_score(backend: ModelBackend, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Score primitive over a raw payload dict."""
    premise = str(_need(payload, "premise"))
    hypothesis = str(_need(payload, "hypothesis"))
    probs = backend.predict_probs([(premise, hypothesis)])
    return _build_judge_score_result(premise, hypothesis, probs[0])


def _run_noul(backend: ModelBackend, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Noul primitive over a raw payload dict."""
    premise = str(_need(payload, "premise"))
    claim = str(_need(payload, "claim"))
    probs = backend.predict_probs([(premise, claim)])
    return _build_noul_result(premise, claim, probs[0])


def _run_choice(backend: ModelBackend, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Choice primitive over a raw payload dict."""
    question = str(_need(payload, "question"))
    options = [str(option) for option in _need(payload, "options")]
    scores = _option_entailment(backend, question, options)
    return _build_choice_result(question, options, scores)


def _run_rerank(backend: ModelBackend, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Rerank primitive over a raw payload dict."""
    question = str(_need(payload, "question"))
    options = [str(option) for option in _need(payload, "options")]
    scores = _option_entailment(backend, question, options)
    return _build_rerank_result(question, options, scores)


def _run_grade(backend: ModelBackend, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Grade primitive over a raw payload dict."""
    question = str(_need(payload, "question"))
    reference = str(_need(payload, "reference"))
    candidate = str(_need(payload, "candidate"))
    probs = backend.predict_probs(
        [(f"{question}\nReference answer: {reference}", f"Answer: {candidate}")]
    )
    label = max(range(len(probs[0])), key=lambda i: probs[0][i])
    return _build_grade_result(
        question,
        reference,
        candidate,
        ("contradiction", "entailment", "neutral")[label],
    )


RUNNERS: dict[str, Callable[[ModelBackend, Mapping[str, Any]], dict[str, Any]]] = {
    "score": _run_score,
    "noul": _run_noul,
    "choice": _run_choice,
    "rerank": _run_rerank,
    "grade": _run_grade,
}


def _backend_for(state: ServerState, model_id: str) -> ModelBackend:
    """Return the resident backend for ``model_id``, constructing it on first use."""
    backend = state.backends.get(model_id)
    if backend is not None:
        return backend
    entry = next(item for item in state.config.models if item.id == model_id)
    from openjev_tool.backend import make_backend

    backend = make_backend(entry, state.device, fake=state.fake)
    state.backends[model_id] = backend
    return backend


def _pin(state: ServerState, endpoint: str, model_id: str) -> RoutingDecision:
    """Resolve an explicit model override to a pinned RoutingDecision."""
    for entry in state.config.models:
        if entry.id != model_id:
            continue
        if not entry.enabled:
            raise HTTPException(status_code=422, detail=f"model '{model_id}' is disabled")
        if not entry.supports(endpoint):
            raise HTTPException(
                status_code=422, detail=f"model '{model_id}' does not support '{endpoint}'"
            )
        return RoutingDecision(model_id=model_id, reason="pinned", candidates=(model_id,))
    raise HTTPException(status_code=404, detail=f"unknown model: '{model_id}'")


def _dispatch(
    state: ServerState, endpoint: str, payload: Mapping[str, Any], state_text: str = ""
) -> dict[str, Any]:
    """Route, run, record stats, and stamp routing info onto the result.

    An optional ``model`` key in the payload pins the routing to one model.

    Raises:
        HTTPException: 422 on invalid payloads, 500 on backend failure.
    """
    enriched = dict(payload)
    pinned_model = str(enriched.pop("model", "") or "")
    if state_text and not enriched.get("premise"):
        enriched["premise"] = state_text
    if pinned_model:
        decision = _pin(state, endpoint, pinned_model)
    else:
        decision = route(endpoint, state.config.models, state.stats.model_summary())
    started = time.perf_counter()
    try:
        result = RUNNERS[endpoint](_backend_for(state, decision.model_id), enriched)
    except HTTPException:
        raise
    except ValueError as exc:
        state.stats.record(endpoint, decision.model_id, time.perf_counter() - started, error=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        state.stats.record(endpoint, decision.model_id, time.perf_counter() - started, error=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    state.stats.record(endpoint, decision.model_id, time.perf_counter() - started)
    result["routing"] = {
        "model": decision.model_id,
        "reason": decision.reason,
        "candidates": list(decision.candidates),
    }
    return result


def _run_ask(state: ServerState, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Level-10 batch: several independent questions over one state."""
    questions = payload.get("questions")
    if not isinstance(questions, list) or not questions:
        raise HTTPException(status_code=422, detail="'questions' must be a non-empty list")
    state_text = str(payload.get("state", ""))
    answers: dict[str, Any] = {}
    for index, question in enumerate(questions):
        if not isinstance(question, dict):
            raise HTTPException(status_code=422, detail=f"questions[{index}] must be an object")
        question_id = str(question.get("id", f"q{index}"))
        endpoint = str(question.get("endpoint", ""))
        if endpoint not in RUNNERS:
            raise HTTPException(
                status_code=422,
                detail=f"questions[{index}].endpoint must be one of {sorted(RUNNERS)}",
            )
        inner_payload = question.get("payload", {})
        if not isinstance(inner_payload, dict):
            raise HTTPException(
                status_code=422, detail=f"questions[{index}].payload must be an object"
            )
        answers[question_id] = _dispatch(state, endpoint, inner_payload, state_text)
    return {"answers": answers}


def _run_invoke(state: ServerState, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Level-based dispatch used by the playground (level 1..10).

    An omitted or empty ``payload`` falls back to the level's built-in
    example, so ``curl -d '{"level": 2}'`` demos the endpoint out of the box.
    """
    try:
        level = level_by_number(int(payload.get("level", 0)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    inner = payload.get("payload", {})
    if not isinstance(inner, dict):
        raise HTTPException(status_code=422, detail="'payload' must be an object")
    if not inner:
        inner = dict(level.example)
    if level.endpoint == "ask":
        result = _run_ask(state, inner)
    else:
        enriched = dict(inner)
        if payload.get("model"):
            enriched["model"] = payload["model"]
        result = _dispatch(state, level.endpoint, enriched)
    return {
        "level": level.number,
        "name": level.name,
        "description": level.description,
        "result": result,
    }


def _model_status(state: ServerState, entry: Any) -> dict[str, Any]:
    """One /v1/models row for a registry entry."""
    from openjev_tool.registry import entry_downloaded

    return {
        "id": entry.id,
        "repo": entry.repo,
        "enabled": entry.enabled,
        "capabilities": list(entry.capabilities),
        "priority": entry.priority,
        "loaded": entry.id in state.backends,
        "downloaded": entry_downloaded(entry),
    }


def build_app(
    config: Config | None = None,
    device: str | None = None,
    fake: bool = False,
) -> FastAPI:
    """Construct the openjev server app.

    Args:
        config: Registry/server config; loaded from disk when omitted.
        device: Torch device override; auto-selected when omitted.
        fake: Use the deterministic FakeBackend instead of torch.

    Returns:
        A configured FastAPI application.
    """
    resolved_config = config if config is not None else load_config()
    resolved_device = (
        device if device is not None else select_device(os.environ.get("OPENJEV_DEVICE") or None)
    )
    persist_path = stats_path() if resolved_config.stats.persist else None
    state = ServerState(
        config=resolved_config,
        device=resolved_device,
        fake=fake,
        stats=StatsCollector(persist_path),
    )

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if state.config.stats.persist:
            state.stats.save()

    app = FastAPI(
        title="openjev server",
        version=server_version(),
        description="Local judgment server for Jev-style NLI scoring.",
        lifespan=_lifespan,
    )

    async def _router_error_handler(_request: Any, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    app.add_exception_handler(RouterError, _router_error_handler)

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Liveness + uptime; the menu bar polls this."""
        return {
            "status": "ok",
            "version": server_version(),
            "uptime_s": round(time.time() - state.stats.started_at, 3),
            "levels": len(LEVELS),
        }

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        """Invocation totals plus per-endpoint/per-model latency and errors."""
        return state.stats.snapshot(resident_models=loaded_model_count())

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        """Registry state: enabled, loaded, capabilities, priority, downloaded."""
        return {"models": [_model_status(state, entry) for entry in state.config.models]}

    @app.get("/v1/levels")
    def levels() -> dict[str, Any]:
        """The 10-level ladder with examples; drives the playground."""
        return {"levels": levels_payload()}

    @app.get("/v1/reload")
    def reload_config() -> dict[str, Any]:
        """Re-read config.json and drop backends for removed/disabled models."""
        state.config = load_config()
        keep = {entry.id for entry in state.config.models if entry.enabled}
        state.backends = {
            model_id: backend for model_id, backend in state.backends.items() if model_id in keep
        }
        return {
            "models": [entry.id for entry in state.config.models],
            "loaded": sorted(state.backends),
        }

    @app.get("/v1/config")
    def get_config() -> dict[str, Any]:
        """The live config document (same shape as config.json on disk)."""
        return state.config.to_dict()

    @app.put("/v1/config")
    def put_config(payload: dict[str, Any]) -> dict[str, Any]:
        """Validate, persist, and apply a full config document.

        Used by the playground settings form and the model checkboxes; writes
        ``~/.config/openjev/config.json`` atomically and hot-applies it.
        """
        try:
            new_config = Config.from_dict(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        save_config(new_config)
        state.config = new_config
        keep = {entry.id for entry in new_config.models if entry.enabled}
        state.backends = {
            model_id: backend for model_id, backend in state.backends.items() if model_id in keep
        }
        return {"server": new_config.server.to_dict(), "models": len(new_config.models)}

    def _make_handler(endpoint: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def handler(payload: dict[str, Any]) -> dict[str, Any]:
            return _dispatch(state, endpoint, payload)

        return handler

    for endpoint_name in RUNNERS:
        handler = _make_handler(endpoint_name)
        app.post(f"/v1/{endpoint_name}", name=f"v1-{endpoint_name}")(handler)
        app.post(f"/{endpoint_name}", name=f"legacy-{endpoint_name}")(handler)

    @app.post("/v1/ask")
    def ask(payload: dict[str, Any]) -> dict[str, Any]:
        """Batch several independent questions over one state (level 10)."""
        return _run_ask(state, payload)

    @app.post("/v1/invoke")
    def invoke(payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch by level 1-10 (playground entry point)."""
        return _run_invoke(state, payload)

    static_root = playground_path()
    if static_root.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/ui", StaticFiles(directory=str(static_root), html=True), name="playground")

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            """Redirect to the playground."""
            return RedirectResponse(url="/ui/")

    return app


def pidfile_path() -> Path:
    """The supervised-server pidfile location."""
    from openjev_tool.stats import state_dir

    return state_dir() / "server.pid"


def run_server(
    host: str | None = None,
    port: int | None = None,
    device: str = "",
    fake: bool = False,
    supervised: bool = False,
) -> None:
    """Run the server under uvicorn (blocking); defaults come from config.

    Args:
        host: Bind address; None/empty resolves to config server.host.
        port: Bind port; None/0 resolves to config server.port.
        device: Torch device override ("" = auto).
        fake: Serve with the deterministic FakeBackend.
        supervised: Write a pidfile so the menu bar can manage this process.
    """
    import uvicorn

    from openjev_tool.config import ensure_config

    ensure_config()
    config = load_config()
    resolved_host = host or config.server.host
    resolved_port = port or config.server.port
    pidfile = _write_pidfile() if supervised else None
    try:
        uvicorn.run(
            build_app(device=device or None, fake=fake),
            host=resolved_host,
            port=resolved_port,
            log_level="warning",
        )
    finally:
        if pidfile is not None:
            pidfile.unlink(missing_ok=True)


def _write_pidfile() -> Path:
    """Write the current pid to the state dir and return the pidfile path."""
    target = pidfile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(os.getpid()), encoding="utf-8")
    return target
