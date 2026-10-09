"""配置 API 回归测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from spark.web.server import AppState, create_app
from tests.test_web import TOKEN, _state


def _client(tmp_path: Path) -> tuple[TestClient, AppState]:
    state = _state(tmp_path)
    return TestClient(create_app(state)), state


def _http_client(tmp_path: Path) -> TestClient:
    return _client(tmp_path)[0]


def test_config_save_persists_fallback_model(tmp_path: Path) -> None:
    """fallback_model 必须被保存且回显（此前漏在字段元组 + 回显里，UI 保存即丢）。"""
    client = _http_client(tmp_path)
    cur = client.get("/api/config", headers={"X-Spark-Token": TOKEN}).json()["current"]
    body = {k: v for k, v in cur.items() if k != "api_key"}
    body["fallback_model"] = "u2-pro"
    r = client.post("/api/config", json=body, headers={"X-Spark-Token": TOKEN})
    assert r.status_code == 200
    got = client.get("/api/config", headers={"X-Spark-Token": TOKEN}).json()["current"]
    assert got["fallback_model"] == "u2-pro"
    # 允许显式清空（此前空串被通用字段逻辑跳过，无法取消 fallback）
    body["fallback_model"] = ""
    client.post("/api/config", json=body, headers={"X-Spark-Token": TOKEN})
    got2 = client.get("/api/config", headers={"X-Spark-Token": TOKEN}).json()["current"]
    assert got2["fallback_model"] == ""


def test_config_save_ignores_masked_key(tmp_path: Path) -> None:
    """打码后的密钥回传必须被忽略，不得覆盖真 key（此前全星号判定漏掉带前缀的脱敏值）。"""
    from spark.config import mask_key

    client, state = _client(tmp_path)
    client.post(
        "/api/config",
        json={"api_key": "sk-1234567890abcdef"},
        headers={"X-Spark-Token": TOKEN},
    )
    masked = mask_key("sk-1234567890abcdef")
    client.post(
        "/api/config",
        json={"api_key": masked},
        headers={"X-Spark-Token": TOKEN},
    )
    assert state.cfg["api_key"] == "sk-1234567890abcdef"
