"""Server API tests via TestClient with the FakeBackend (no torch, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openjev_tool.config import (
    DEFAULT_ENTRY_ID,
    Config,
    ModelEntry,
    default_config,
    load_config,
    save_config,
)
from openjev_tool.server import build_app, pidfile_path, run_server


@pytest.fixture
def config() -> Config:
    cfg = default_config()
    cfg.stats.persist = False
    return cfg


@pytest.fixture
def client(config: Config) -> TestClient:
    return TestClient(build_app(config=config, device="cpu", fake=True))


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["levels"] == 10
    assert "version" in body


def test_score_and_legacy_alias(client: TestClient) -> None:
    payload = {"premise": "a man plays guitar", "hypothesis": "a man plays guitar"}
    for path in ("/v1/score", "/score"):
        response = client.post(path, json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["routing"]["model"] == DEFAULT_ENTRY_ID
        assert body["score"] > 0


def test_missing_field_is_422(client: TestClient) -> None:
    response = client.post("/v1/score", json={"premise": "p"})
    assert response.status_code == 422


def test_router_503_when_no_capable_model() -> None:
    config = Config(models=[ModelEntry(id="noul-only", repo="org/x", capabilities=("noul",))])
    config.stats.persist = False
    client = TestClient(build_app(config=config, device="cpu", fake=True))
    response = client.post("/v1/rerank", json={"question": "q?", "options": ["a", "b"]})
    assert response.status_code == 503
    assert "no enabled model supports" in response.json()["detail"]


def test_stats_counts_invocations(client: TestClient) -> None:
    client.post("/v1/score", json={"premise": "p", "hypothesis": "h"})
    client.post("/v1/noul", json={"premise": "p", "claim": "c"})
    body = client.get("/stats").json()
    assert body["totals"]["invocations"] == 2
    assert body["endpoints"]["score"]["count"] == 1
    assert body["models"][DEFAULT_ENTRY_ID]["count"] == 2


def test_models_listing(client: TestClient) -> None:
    body = client.get("/v1/models").json()
    ids = [model["id"] for model in body["models"]]
    assert DEFAULT_ENTRY_ID in ids
    default = next(m for m in body["models"] if m["id"] == DEFAULT_ENTRY_ID)
    assert default["enabled"] is True
    assert "score" in default["capabilities"]


def test_levels_listing(client: TestClient) -> None:
    assert len(client.get("/v1/levels").json()["levels"]) == 10


def test_invoke_dispatches_level(client: TestClient) -> None:
    response = client.post(
        "/v1/invoke",
        json={"level": 1, "payload": levels_payload_item(1)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["level"] == 1
    assert body["name"] == "Basic Decision"
    assert "holds" in body["result"]
    assert body["result"]["routing"]["model"] == DEFAULT_ENTRY_ID


def test_invoke_without_payload_uses_level_example(client: TestClient) -> None:
    response = client.post("/v1/invoke", json={"level": 10})
    assert response.status_code == 200, response.text
    assert set(response.json()["result"]["answers"]) == {"failure", "simple_fix", "risk"}


def test_invoke_rejects_bad_level(client: TestClient) -> None:
    response = client.post("/v1/invoke", json={"level": 42})
    assert response.status_code == 422


def test_invoke_level_10_uses_ask(client: TestClient) -> None:
    response = client.post("/v1/invoke", json={"level": 10, "payload": levels_payload_item(10)})
    assert response.status_code == 200, response.text
    answers = response.json()["result"]["answers"]
    assert set(answers) == {"failure", "simple_fix", "risk"}


def test_ask_uses_state_as_premise_fallback(client: TestClient) -> None:
    response = client.post(
        "/v1/ask",
        json={
            "state": "The refund policy is 30 days",
            "questions": [
                {
                    "id": "q1",
                    "endpoint": "noul",
                    "payload": {"claim": "The refund policy is 30 days"},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["answers"]["q1"]["verdict"] == "true"


def test_ask_rejects_bad_endpoint(client: TestClient) -> None:
    response = client.post(
        "/v1/ask",
        json={"questions": [{"id": "q", "endpoint": "chat", "payload": {}}]},
    )
    assert response.status_code == 422


def test_reload_picks_up_config_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("OPENJEV_CONFIG", str(config_path))
    config = default_config()
    config.stats.persist = False
    save_config(config, config_path)

    client = TestClient(build_app(config=config, device="cpu", fake=True))
    config.models.append(ModelEntry(id="extra", repo="org/extra"))
    save_config(config, config_path)

    body = client.get("/v1/reload").json()
    assert "extra" in body["models"]


def test_playground_served_and_root_redirects(client: TestClient) -> None:
    page = client.get("/ui/")
    assert page.status_code == 200
    assert "openjev playground" in page.text
    redirected = client.get("/", follow_redirects=False)
    assert redirected.status_code in (301, 302, 307)
    assert redirected.headers["location"] == "/ui/"


def test_config_get_and_put_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setenv("OPENJEV_CONFIG", str(config_path))
    config = default_config()
    config.stats.persist = False
    save_config(config, config_path)

    client = TestClient(build_app(config=config, device="cpu", fake=True))
    live = client.get("/v1/config").json()
    assert live["server"]["port"] == 8080

    live["server"]["port"] = 9099
    response = client.put("/v1/config", json=live)
    assert response.status_code == 200, response.text
    assert load_config(config_path).server.port == 9099
    assert client.get("/v1/config").json()["server"]["port"] == 9099


def test_config_put_rejects_invalid_document(client: TestClient) -> None:
    response = client.put("/v1/config", json={"version": 99})
    assert response.status_code == 422


def test_invoke_model_pin(client: TestClient) -> None:
    payload = {"level": 1, "model": DEFAULT_ENTRY_ID, "payload": levels_payload_item(1)}
    response = client.post("/v1/invoke", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["routing"]["reason"] == "pinned"


def test_invoke_model_pin_unknown_is_404(client: TestClient) -> None:
    response = client.post(
        "/v1/invoke", json={"level": 1, "model": "ghost", "payload": levels_payload_item(1)}
    )
    assert response.status_code == 404


def test_invoke_model_pin_unsupported_is_422() -> None:
    config = Config(models=[ModelEntry(id="noul-only", repo="org/x", capabilities=("noul",))])
    config.stats.persist = False
    client = TestClient(build_app(config=config, device="cpu", fake=True))
    response = client.post(
        "/v1/invoke", json={"level": 2, "model": "noul-only", "payload": levels_payload_item(2)}
    )
    assert response.status_code == 422
    assert "does not support" in response.json()["detail"]


def test_run_server_writes_and_removes_pidfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENJEV_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENJEV_CONFIG", str(tmp_path / "config.json"))
    save_config(default_config(), tmp_path / "config.json")
    launched: list[tuple[str, int]] = []

    def _fake_launch(app: object, host: str, port: int, **_kwargs: object) -> None:
        launched.append((host, port))

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", _fake_launch)
    run_server(host="127.0.0.1", port=9999, fake=True, supervised=True)
    assert launched == [("127.0.0.1", 9999)]
    assert not pidfile_path().exists()


def levels_payload_item(number: int) -> dict:
    """The example payload of a level, unwrapped for direct use."""
    from openjev_tool.levels import levels_payload

    return next(level["example"] for level in levels_payload() if level["number"] == number)
