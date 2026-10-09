"""检查点测试：git 探测、自动提交、reset 回滚。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from spark.approval import ApprovalGate
from spark.loop import AgentLoop
from spark.tools.git import git_commit, git_reset, is_git_repo

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
    # 先制造未提交改动：工作区干净时本就不需要检查点（git 会返回 nothing to commit）
    (tmp_path / "b.txt").write_text("draft", encoding="utf-8")
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
    # 检查点记录的是写之前的状态：HEAD 就是那个检查点，a.txt 仍是 v1
    assert "b.txt" in _git(tmp_path, "show", "--name-only", "--oneline", "HEAD")
    assert _git(tmp_path, "show", "HEAD:a.txt") == "v1"


async def test_checkpoint_excludes_runtime_artifacts(tmp_path: Path) -> None:
    """自动检查点不得把 .venv/记忆库等运行产物提交进仓库（且解跟踪不删文件）。"""
    _make_repo(tmp_path)
    (tmp_path / ".venv" / "Lib").mkdir(parents=True)
    (tmp_path / ".venv" / "Lib" / "pyvenv.cfg").write_text("home=x", encoding="utf-8")
    (tmp_path / ".spark-home").mkdir()
    (tmp_path / ".spark-home" / "memory.db").write_text("sqlite", encoding="utf-8")
    (tmp_path / "a.txt").write_text("v2", encoding="utf-8")

    ok, text = await git_commit(tmp_path, "checkpoint")
    assert ok, text
    assert "已排除运行产物" in text, text

    tracked = _git(tmp_path, "ls-files")
    assert ".venv" not in tracked and "memory.db" not in tracked, tracked
    assert "a.txt" in tracked
    # 解跟踪只动索引，工作区文件必须还在
    assert (tmp_path / ".venv" / "Lib" / "pyvenv.cfg").exists()
    assert (tmp_path / ".spark-home" / "memory.db").exists()
    # 本地 exclude，不污染用户 .gitignore
    exclude = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".venv/" in exclude and ".spark-home/" in exclude
    assert not (tmp_path / ".gitignore").exists()


async def test_reset_leaves_excluded_artifacts_untouched(tmp_path: Path) -> None:
    """已跟踪的运行产物会让 reset --hard 失败；解跟踪后回滚只回滚源码。"""
    _make_repo(tmp_path)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "marker.txt").write_text("dep", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "把依赖也提交了")
    (tmp_path / "a.txt").write_text("v2", encoding="utf-8")

    ok, text = await git_reset(tmp_path)
    assert ok, text
    assert (tmp_path / "a.txt").read_text() == "v1"          # 源码回滚
    assert (tmp_path / ".venv" / "marker.txt").exists()      # 产物保留


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
