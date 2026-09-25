"""TUI 测试（Textual pilot）：消息渲染与审批交互。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from spark2.memory import MemoryStore
from spark2.store import SessionStore
from spark2.tui import SparkTui

try:
    from textual.widgets import Input

    TEXTUAL_OK = True
except ImportError:  # pragma: no cover
    TEXTUAL_OK = False

pytestmark = pytest.mark.skipif(not TEXTUAL_OK, reason="需要 textual")


def _cfg(tmp_path: Path, **extra) -> dict:
    cfg = {
        "provider": "mock",
        "base_url": "",
        "model": "mock",
        "api_key": "",
        "workdir": str(tmp_path),
        "approval_mode": "suggest",
        "max_context_tokens": 32000,
        "token": "t",
    }
    cfg.update(extra)
    return cfg


async def _wait_for(app: SparkTui, needle: str, timeout: float = 8.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        box = app.query_one("#messages")
        for c in box.children:
            try:
                if needle in str(c.render()):
                    return True
            except Exception:  # noqa: BLE001
                continue
    return False


async def _submit(app: SparkTui, text: str) -> None:
    inp = app.query_one("#input", Input)
    inp.value = text
    await inp.action_submit()


async def test_tui_demo_message(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    app = SparkTui(cfg=cfg, store=SessionStore(root=tmp_path / "sessions"), memory=MemoryStore(path=tmp_path / "mem.db"))
    async with app.run_test() as pilot:
        assert app.sid is not None  # 自动建了会话
        await _submit(app, "你好，介绍一下")
        ok = await _wait_for(app, "演示模式")
        assert ok, "应渲染助手回复（演示模式文本）"
        # 助手消息已落盘
        msgs = app.store.messages(app.sid)
        assert msgs[-1]["role"] == "assistant"


async def test_tui_approval_flow(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, mock_script=[
        [{"type": "tool_calls", "calls": [{"id": "w1", "name": "write_file", "arguments": {"path": "a.txt", "content": "hi"}}]}],
        [{"type": "text", "text": "写好了。"}],
    ])
    app = SparkTui(cfg=cfg, store=SessionStore(root=tmp_path / "sessions"), memory=MemoryStore(path=tmp_path / "mem.db"))
    async with app.run_test() as pilot:
        await _submit(app, "写个文件")
        # 等待审批条出现
        for _ in range(160):
            await asyncio.sleep(0.05)
            if app.query_one("#approval").has_class("visible"):
                break
        bar = app.query_one("#approval")
        assert bar.has_class("visible"), "应弹出审批条"
        await pilot.press("a")  # 允许
        ok = await _wait_for(app, "写好了。")
        assert ok
        assert (tmp_path / "a.txt").read_text() == "hi"
