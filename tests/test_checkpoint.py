"""检查点测试：git 探测、自动提交、reset 回滚。"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from spark2.approval import ApprovalGate
from spark2.loop import AgentLoop
from spark2.tools.git import git_commit, git_reset, is_git_repo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")

TOOL_WRITE = [
    {
        "type": "tool_calls",
        "calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "v2"}}],
    }
]
TEXT_DONE = [{"type": "text", "text": "完成。"}]


def _git(wd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(wd), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return (r.stdout or "").strip()


def _make_repo(wd: Path) -> None:
    _git(wd, "init", "-q")
    _git(wd, "config", "user.email", "t@t")
    _git(wd, "config", "user.name", "t")
    (wd / "a.txt").write_text("v1", encoding="utf-8")
    _git(wd, "add", "-A")
    _git(wd, "commit", "-q", "-m", "init")


async def test_is_git_repo_and_commit(tmp_path: Path) -> None:
    assert not is_git_repo(tmp_path)
    _make_repo(tmp_path)
    assert is_git_repo(tmp_path)
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    ok, text = await git_commit(tmp_path, "checkpoint 1")
    assert ok, text
    log = _git(tmp_path, "log", "--oneline")
    assert "checkpoint 1" in log


async def test_auto_checkpoint_before_write(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    loop = AgentLoop(
        tmp_path,
        {"model": "mock", "mock_script": [TOOL_WRITE, TEXT_DONE]},
        ApprovalGate(mode="suggest"),
    )
    got: list[dict] = []

    async def run():
        async for ev in loop.stream([{"role": "user", "content": "写文件"}]):
            got.append(ev)
            if ev["type"] == "approval":
                loop.gate.respond(ev["request_id"], "allow")

    await run()
    assert (tmp_path / "a.txt").read_text() == "v2"
    log = _git(tmp_path, "log", "--oneline")
    assert any("前检查点" in line for line in log.splitlines()), log


async def test_reset_rolls_back_to_checkpoint(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    loop = AgentLoop(
        tmp_path,
        {"model": "mock", "mock_script": [
            TOOL_WRITE,
            [{"type": "tool_calls", "calls": [{"id": "c2", "name": "reset", "arguments": {}}]}],
            TEXT_DONE,
        ]},
        ApprovalGate(mode="suggest"),
    )
    got: list[dict] = []

    async def run():
        async for ev in loop.stream([{"role": "user", "content": "先写再回滚"}]):
            got.append(ev)
            if ev["type"] == "approval":
                loop.gate.respond(ev["request_id"], "allow")

    await run()
    # 写 → 自动检查点（v1）→ reset --hard HEAD → 回到 v1
    assert (tmp_path / "a.txt").read_text() == "v1"
    rs = [e for e in got if e["type"] == "tool_result" and e["name"] == "reset"]
    assert rs and "回滚" in rs[0]["output"]


async def test_reset_denied_keeps_changes(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    loop = AgentLoop(
        tmp_path,
        {"model": "mock", "mock_script": [
            [{"type": "tool_calls", "calls": [{"id": "c1", "name": "reset", "arguments": {}}]}],
            TEXT_DONE,
        ]},
        ApprovalGate(mode="suggest"),
    )
    got: list[dict] = []
    # 故意不响应审批 → 超时默认拒绝（这里直接 deny）
    async def run():
        async for ev in loop.stream([{"role": "user", "content": "回滚"}]):
            got.append(ev)
            if ev["type"] == "approval":
                loop.gate.respond(ev["request_id"], "deny")

    await run()
    tr = next(e for e in got if e["type"] == "tool_result")
    assert tr["approved"] is False
    # 没执行 reset，初始提交内容不变
    assert (tmp_path / "a.txt").read_text() == "v1"


async def test_git_reset_not_repo(tmp_path: Path) -> None:
    ok, text = await git_reset(tmp_path)
    assert not ok and "不是 git 仓库" in text
