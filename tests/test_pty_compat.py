"""平台兼容回归：ptyprocess 不可用（如 Windows 无 fcntl）时——
1) spark2.web.server 仍可导入、Web 服务可创建；
2) PtySession 创建抛出明确的 RuntimeError；
3) /ws/pty 返回 {"type":"err"} 提示而非崩溃。
与 tests/test_pty.py 的约定一致：Windows P1 不支持内置终端，跳过而非拒绝启动。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import spark2.pty as pty_mod
from spark2.store import SessionStore
from spark2.web.server import AppState, create_app

TOKEN = "test-token"


def _client(tmp_path: Path) -> tuple[TestClient, AppState]:
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
    state = AppState(cfg=cfg, store=SessionStore(root=tmp_path / "sessions"))
    return TestClient(create_app(state)), state


def test_web_importable_and_page_ok(tmp_path: Path) -> None:
    """任何平台上 Web 服务都能创建并返回页面（当前 Windows 因 pty 导入而挂）。"""
    client, _ = _client(tmp_path)
    assert client.get("/").status_code == 200


def test_pty_session_raises_when_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pty_mod, "PtyProcess", None)
    with pytest.raises(RuntimeError):
        pty_mod.PtySession("tab-x", tmp_path)


def test_ws_pty_returns_err_when_unavailable(tmp_path: Path, monkeypatch) -> None:
    client, state = _client(tmp_path)

    def _boom(sid: str, tab_id: str, cwd: Path):
        raise RuntimeError("内置终端在当前平台不可用")

    monkeypatch.setattr(state.pty, "get_or_create", _boom)
    with client.websocket_connect(f"/ws/pty?token={TOKEN}") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "err"
        assert "终端" in msg["message"]


# ---------- Windows ConPTY 后端（pywinpty）：内置终端在 Windows 真正可用 ----------

import sys  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.skipif(sys.platform != "win32", reason="ConPTY 后端仅 Windows")
def test_windows_pty_echo_and_cwd(tmp_path: Path) -> None:
    """Windows 上 PTY 不再是"设计性缺失"：spawn cmd.exe，回显与 cwd 绑定可用。"""
    import asyncio

    from spark2.pty import PTY_AVAILABLE, PtyManager

    if not PTY_AVAILABLE:
        pytest.skip("pywinpty 未安装")
    m = PtyManager()
    sess = m.get_or_create("ws1", "wt1", tmp_path)
    chunks: list[str] = []

    async def main() -> None:
        q: asyncio.Queue[str] = asyncio.Queue()
        sess.start_reader(asyncio.get_running_loop(), q)
        await asyncio.sleep(1.2)  # 等 cmd 启动序列
        sess.resize(100, 30)
        sess.write("echo SPARK_WIN_PTY\r")
        await asyncio.sleep(0.6)
        sess.write("cd\r")  # cmd 无参 cd = 打印当前目录
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            try:
                d = await asyncio.wait_for(q.get(), timeout=1.0)
                chunks.append(d)
            except asyncio.TimeoutError:
                pass
            joined = "".join(chunks)
            if (
                joined.count("SPARK_WIN_PTY") >= 2
                and str(tmp_path).lower() in joined.lower()
            ):
                break
        sess.write("exit\r")
        await asyncio.sleep(0.5)

    try:
        asyncio.run(main())
    finally:
        m.close_all()
    joined = "".join(chunks)
    assert joined.count("SPARK_WIN_PTY") >= 2  # 命令回显 + 输出
    assert str(tmp_path).lower() in joined.lower()  # cwd 绑定到工作目录
