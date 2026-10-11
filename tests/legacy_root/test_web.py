"""Web 链路测试：鉴权、页面、配置、会话、SSE 演示流、审批接口。

注意：进程内测试传输层（TestClient / httpx ASGITransport）都是"先收完整个响应体、
再返回给客户端"，因此在测试里无法模拟"流打开期间并发回发审批"（会死锁）。
审批在流内的并发回发由 spark 的 uvicorn 真实服务器承担，用真端口手动验证；
本文件覆盖：鉴权/配置/会话/SSE 事件格式/审批接口的响应逻辑。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from spark.store import SessionStore
from spark.web.server import AppState, create_app

TOKEN = "test-token"


def _state(tmp_path: Path) -> AppState:
    cfg = {
        "provider": "mock",
        "base_url": "",
        "model": "mock",
        "api_key": "",
        "workdir": str(tmp_path),
        "approval_mode": "suggest",
        "max_context_tokens": 32000,
        "token": TOKEN,
        "mock_script": None,
    }
    return AppState(cfg=cfg, store=SessionStore(root=tmp_path / "sessions"))


def _client(tmp_path: Path) -> tuple[TestClient, AppState]:
    state = _state(tmp_path)
    return TestClient(create_app(state)), state


def test_page_and_auth(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="root"' in r.text
    assert "Spark 编程助手" not in r.text
    assert client.get("/api/config").status_code == 401
    r = client.get("/api/config", headers={"X-Spark-Token": TOKEN})
    assert r.status_code == 200
    assert r.json()["current"]["workdir"] == str(tmp_path)


def test_config_save_masks_key(tmp_path: Path) -> None:
    client, state = _client(tmp_path)
    r = client.post(
        "/api/config",
        headers={"X-Spark-Token": TOKEN},
        json={"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key": "sk-1234567890"},
    )
    assert r.status_code == 200
    cur = r.json()["current"]
    assert cur["api_key"] == "sk-1********7890"
    assert state.cfg["api_key"] == "sk-1234567890"
    # 打码值再次提交不应覆盖真实密钥
    client.post(
        "/api/config",
        headers={"X-Spark-Token": TOKEN},
        json={"api_key": "****"},
    )
    assert state.cfg["api_key"] == "sk-1234567890"


def test_sessions_crud(tmp_path: Path) -> None:
    client, state = _client(tmp_path)
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)})
    assert r.status_code == 200
    sid = r.json()["id"]
    r = client.get("/api/sessions", headers={"X-Spark-Token": TOKEN})
    assert any(s["id"] == sid for s in r.json())
    r = client.get(f"/api/sessions/{sid}", headers={"X-Spark-Token": TOKEN})
    assert r.status_code == 200
    assert r.json()["meta"]["id"] == sid
    # 删除会话
    r = client.delete(f"/api/sessions/{sid}", headers={"X-Spark-Token": TOKEN})
    assert r.status_code == 200
    assert not (tmp_path / "sessions" / sid).exists()
    r = client.delete(f"/api/sessions/{sid}", headers={"X-Spark-Token": TOKEN})
    assert r.status_code == 404
    # 配置与请求都没有工作目录时 → 400
    state.cfg["workdir"] = ""
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": ""})
    assert r.status_code == 400


def test_session_rename(tmp_path: Path) -> None:
    client, state = _client(tmp_path)
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)})
    sid = r.json()["id"]
    r = client.patch(f"/api/sessions/{sid}", headers={"X-Spark-Token": TOKEN}, json={"title": "重构登录模块"})
    assert r.status_code == 200
    assert state.store.meta(sid)["title"] == "重构登录模块"
    # 空标题 → 400；不存在的会话 → 404
    assert client.patch(f"/api/sessions/{sid}", headers={"X-Spark-Token": TOKEN}, json={"title": "   "}).status_code == 400
    assert client.patch("/api/sessions/nope", headers={"X-Spark-Token": TOKEN}, json={"title": "x"}).status_code == 404


def test_config_saves_mcp_servers(tmp_path: Path) -> None:
    """Web 设置保存 MCP 服务器列表 → 落配置并重建管理器。"""
    client, state = _client(tmp_path)
    r = client.post(
        "/api/config",
        headers={"X-Spark-Token": TOKEN},
        json={
            "mcp_servers": [
                {"name": "fs", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], "env": {}}
            ]
        },
    )
    assert r.status_code == 200
    servers = r.json()["mcp"]["servers"]
    assert len(servers) == 1 and servers[0]["name"] == "fs"
    assert state.cfg["mcp_servers"][0]["command"] == "npx"
    assert state.mcp.servers[0].name == "fs"  # 管理器已重建
    # 非法条目被清洗
    client.post(
        "/api/config",
        headers={"X-Spark-Token": TOKEN},
        json={"mcp_servers": [{"name": "", "command": ""}, {"name": "ok", "command": "python"}]},
    )
    cleaned = state.cfg["mcp_servers"]
    assert len(cleaned) == 1 and cleaned[0]["name"] == "ok"


def test_demo_stream_events(tmp_path: Path) -> None:
    """SSE 演示流（无工具调用）：事件格式与结束帧。"""
    client, state = _client(tmp_path)
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)})
    sid = r.json()["id"]
    with client.stream(
        "POST",
        "/api/chat/stream",
        headers={"X-Spark-Token": TOKEN},
        json={"session_id": sid, "prompt": "你好"},
    ) as resp:
        assert resp.status_code == 200
        frames = []
        buf = ""
        for line in resp.iter_lines():
            if line == "":
                if buf.startswith("data:"):
                    frames.append(json.loads(buf[5:].strip()))
                buf = ""
            else:
                buf += line + "\n"
    types = [f["type"] for f in frames]
    assert types[0] == "hello"
    assert "text" in types and "done" in types
    assert frames[-1]["type"] == "close"
    # 会话已落盘：用户与助手消息都在
    msgs = state.store.messages(sid)
    assert msgs[0]["role"] == "user"
    assert any(m["role"] == "assistant" for m in msgs)


def test_chat_stream_sends_current_prompt_to_model(tmp_path: Path, monkeypatch) -> None:
    """真实模型必需：发给模型的消息必须包含当前用户输入（首轮与后续轮都如此）。

    回归：先前先取历史再落盘 prompt，导致首轮请求无 user 消息（mock 不校验所以长期未暴露）。
    """
    captured: list[list[dict]] = []

    async def fake_stream_chat(cfg, messages, tools=None, max_delta=None):
        captured.append([dict(m) for m in messages])
        yield {"type": "text", "text": "好的。"}
        yield {
            "type": "usage",
            "estimated": 10,
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "model": cfg.get("model"),
        }

    monkeypatch.setattr("spark.loop.stream_chat", fake_stream_chat)
    client, state = _client(tmp_path)
    sid = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)}).json()["id"]

    with client.stream(
        "POST", "/api/chat/stream",
        headers={"X-Spark-Token": TOKEN},
        json={"session_id": sid, "prompt": "你好，列出当前目录"},
    ) as resp:
        assert resp.status_code == 200
        resp.read()

    assert captured, "stream_chat 未被调用"
    first = captured[0]
    assert first, "发给模型的消息列表为空"
    assert first[-1]["role"] == "user"
    assert "列出当前目录" in first[-1]["content"]

    # 第二轮：历史 + 本次 user 消息齐全（首轮 user/assistant + 本轮 user）
    with client.stream(
        "POST", "/api/chat/stream",
        headers={"X-Spark-Token": TOKEN},
        json={"session_id": sid, "prompt": "再查一下文件"},
    ) as resp:
        assert resp.status_code == 200
        resp.read()
    second = captured[-1]
    assert second[0]["role"] == "system"  # 循环固定前置 system
    assert [m["role"] for m in second[-3:]] == ["user", "assistant", "user"]
    assert "再查一下文件" in second[-1]["content"]


async def test_approval_endpoint_respond(tmp_path: Path) -> None:
    """审批接口：对已注册的审批请求做 allow/always；未知请求 404；非法 action 400。"""
    client, state = _client(tmp_path)
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)})
    sid = r.json()["id"]
    gate = state.gate(sid)
    # 模拟循环先注册（与服务端 register-before-yield 一致）
    fut = gate.register("req-1", tool_name="run_shell")
    r = client.post("/api/approval", headers={"X-Spark-Token": TOKEN},
                    json={"request_id": "req-1", "action": "allow", "tool": "run_shell"})
    assert r.status_code == 200
    await asyncio.sleep(0)  # 让 call_soon_threadsafe 的回调在测试循环里落地
    assert fut.done() and fut.result() is True

    fut2 = gate.register("req-2", tool_name="run_shell")
    r = client.post("/api/approval", headers={"X-Spark-Token": TOKEN},
                    json={"request_id": "req-2", "action": "always", "tool": "run_shell"})
    assert r.status_code == 200
    await asyncio.sleep(0)
    assert fut2.done() and fut2.result() is True
    assert "run_shell" in gate.always

    r = client.post("/api/approval", headers={"X-Spark-Token": TOKEN},
                    json={"request_id": "nope", "action": "allow"})
    assert r.status_code == 404
    r = client.post("/api/approval", headers={"X-Spark-Token": TOKEN},
                    json={"request_id": "req-3", "action": "maybe"})
    assert r.status_code == 400


def test_cancel_endpoint(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.post("/api/cancel", headers={"X-Spark-Token": TOKEN}, json={"session_id": "nope"})
    assert r.status_code == 404


def test_connection_test_endpoint(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.post("/api/test-connection", headers={"X-Spark-Token": TOKEN},
                    json={"base_url": "", "model": "mock", "api_key": ""})
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_chat_stream_with_images_multimodal(tmp_path: Path, monkeypatch) -> None:
    """多模态识图：body 带 images（base64）时，落盘与发给模型的消息为 OpenAI 兼容 content list。"""
    captured: list[list[dict]] = []

    async def fake_stream_chat(cfg, messages, tools=None, max_delta=None):
        captured.append([dict(m) for m in messages])
        yield {"type": "text", "text": "这是 PNG 图片，内容是登录页设计稿。"}
        yield {"type": "usage", "estimated": 10, "prompt_tokens": 8, "completion_tokens": 2, "model": "mock"}

    monkeypatch.setattr("spark.loop.stream_chat", fake_stream_chat)
    client, state = _client(tmp_path)
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)})
    sid = r.json()["id"]
    b64 = "aGVsbG8="  # "hello" 的 base64，占位图片数据
    with client.stream(
        "POST",
        "/api/chat/stream",
        headers={"X-Spark-Token": TOKEN},
        json={
            "session_id": sid,
            "prompt": "帮我看看这张设计稿",
            "images": [{"data": b64, "mime": "image/png"}],
        },
    ) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_lines():
            pass
    # 落盘消息为 content list（text + image_url）
    msgs = state.store.messages(sid)
    assert isinstance(msgs[0]["content"], list)
    assert msgs[0]["content"][0] == {"type": "text", "text": "帮我看看这张设计稿"}
    assert msgs[0]["content"][1]["type"] == "image_url"
    assert msgs[0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    # 发给模型的 payload 同样包含 image_url
    assert captured
    user_msg = next(m for m in captured[0] if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])


def test_chat_stream_images_keep_slash_expansion(tmp_path: Path, monkeypatch) -> None:
    """斜杠命令 + 贴图同时出现时，文本部分必须是展开后的模板（曾回退成原始 "/explain"）。"""
    captured: list[list[dict]] = []

    async def fake_stream_chat(cfg, messages, tools=None, max_delta=None):
        captured.append([dict(m) for m in messages])
        yield {"type": "text", "text": "已解释。"}
        yield {"type": "usage", "estimated": 5, "prompt_tokens": 4, "completion_tokens": 1, "model": "mock"}

    monkeypatch.setattr("spark.loop.stream_chat", fake_stream_chat)
    client, state = _client(tmp_path)
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)})
    sid = r.json()["id"]
    with client.stream(
        "POST",
        "/api/chat/stream",
        headers={"X-Spark-Token": TOKEN},
        json={
            "session_id": sid,
            "prompt": "/explain 这个文件",
            "images": [{"data": "aGVsbG8=", "mime": "image/png"}],
        },
    ) as resp:
        assert resp.status_code == 200
        for _ in resp.iter_lines():
            pass
    text_part = state.store.messages(sid)[0]["content"][0]
    assert text_part["type"] == "text"
    assert text_part["text"] != "/explain 这个文件"
    assert "这个文件" in text_part["text"] and "解释" in text_part["text"]
    user_msg = next(m for m in captured[0] if m["role"] == "user")
    assert "解释" in user_msg["content"][0]["text"]


def test_chat_stream_rejects_too_many_images(tmp_path: Path) -> None:
    """图片上限：超过 3 张拒绝。"""
    client, state = _client(tmp_path)
    r = client.post("/api/sessions", headers={"X-Spark-Token": TOKEN}, json={"workdir": str(tmp_path)})
    sid = r.json()["id"]
    imgs = [{"data": "eA==", "mime": "image/png"}] * 4
    r = client.post(
        "/api/chat/stream",
        headers={"X-Spark-Token": TOKEN},
        json={"session_id": sid, "prompt": "x", "images": imgs},
    )
    assert r.status_code == 400


def test_mock_chat_accepts_content_list(tmp_path: Path) -> None:
    """mock provider 对 content list（多模态消息）不崩溃且正确取文本。"""
    from spark.provider import stream_chat

    async def run():
        cfg = {"model": "mock", "mock_script": None}
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "帮我看看这张图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,eA=="}},
                ],
            }
        ]
        out = []
        async for ev in stream_chat(cfg, msgs, tools=None):
            out.append(ev)
        return out

    evs = asyncio.run(run())
    texts = [e.get("text", "") for e in evs if e["type"] == "text"]
    assert texts and "演示模式" in texts[0]
