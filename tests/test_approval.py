"""审批门测试：档位 × 路径规则。"""
from __future__ import annotations

import asyncio
from pathlib import Path

from spark2.approval import ApprovalGate
from spark2.tools.base import Tool
from spark2.tools import build_registry


def _tool(name: str, category: str) -> Tool:
    return Tool(
        name=name,
        description="t",
        parameters={"type": "object", "properties": {}},
        category=category,
        handler=lambda args, ctx: "ok",
    )


def test_decide_read_and_system_allow(tmp_path: Path) -> None:
    reg = build_registry()
    gate = ApprovalGate(mode="suggest")
    assert gate.decide(reg["read_file"], {"path": "a.txt"}, tmp_path, [])[0] == "allow"
    assert gate.decide(reg["update_plan"], {"steps": ["x"]}, tmp_path, [])[0] == "allow"


def test_decide_write_inside_suggest_asks(tmp_path: Path) -> None:
    reg = build_registry()
    gate = ApprovalGate(mode="suggest")
    d, _ = gate.decide(reg["write_file"], {"path": "a.txt", "content": "x"}, tmp_path, [])
    assert d == "ask"


def test_decide_write_auto_edit_allows_inside_asks_outside(tmp_path: Path) -> None:
    reg = build_registry()
    gate = ApprovalGate(mode="auto-edit")
    (tmp_path / "in").mkdir()
    d, _ = gate.decide(reg["write_file"], {"path": "in/a.txt", "content": "x"}, tmp_path, [])
    assert d == "allow"
    outside = tmp_path.parent / "outside.txt"
    d, _ = gate.decide(reg["write_file"], {"path": str(outside), "content": "x"}, tmp_path, [])
    assert d == "ask"


def test_decide_write_protected_denies(tmp_path: Path) -> None:
    reg = build_registry()
    gate = ApprovalGate(mode="full-auto")  # full-auto 也不能写保护路径
    protected = [tmp_path / ".git"]
    (tmp_path / ".git").mkdir()
    d, reason = gate.decide(reg["write_file"], {"path": ".git/config", "content": "x"}, tmp_path, protected)
    assert d == "deny"
    assert "保护" in reason


def test_decide_shell_asks_unless_full_auto(tmp_path: Path) -> None:
    reg = build_registry()
    assert ApprovalGate(mode="suggest").decide(reg["run_shell"], {"command": "ls"}, tmp_path, [])[0] == "ask"
    assert ApprovalGate(mode="auto-edit").decide(reg["run_shell"], {"command": "ls"}, tmp_path, [])[0] == "ask"
    assert ApprovalGate(mode="full-auto").decide(reg["run_shell"], {"command": "ls"}, tmp_path, [])[0] == "allow"


def test_always_rule(tmp_path: Path) -> None:
    reg = build_registry()
    gate = ApprovalGate(mode="suggest")
    gate.always.add("run_shell")
    assert gate.decide(reg["run_shell"], {"command": "ls"}, tmp_path, [])[0] == "allow"


def test_always_and_full_auto_never_bypass_path_rules(tmp_path: Path) -> None:
    """路径边界优先于档位与"始终允许"：区外写入即使 always/full-auto 也仍询问。"""
    reg = build_registry()
    outside = tmp_path.parent / "outside.txt"
    gate = ApprovalGate(mode="suggest")
    gate.always.add("write_file")
    d, reason = gate.decide(reg["write_file"], {"path": str(outside), "content": "x"}, tmp_path, [])
    assert d == "ask"
    assert "区外" in reason or "之外" in reason
    # full-auto 同样不越界
    gate2 = ApprovalGate(mode="full-auto")
    gate2.always.add("write_file")
    d2, _ = gate2.decide(reg["write_file"], {"path": str(outside), "content": "x"}, tmp_path, [])
    assert d2 == "ask"
    # 工作区内 + always → 放行
    d3, _ = gate.decide(reg["write_file"], {"path": "in.txt", "content": "x"}, tmp_path, [])
    assert d3 == "allow"


async def test_respond_flow(tmp_path: Path) -> None:
    gate = ApprovalGate(mode="suggest")
    req = "r1"
    fut = asyncio.get_running_loop().create_task(gate.request(req, timeout=10))
    await asyncio.sleep(0.01)
    assert gate.respond(req, "allow") is True
    assert await fut is True

    req2 = "r2"
    fut2 = asyncio.get_running_loop().create_task(gate.request(req2, timeout=10))
    await asyncio.sleep(0.01)
    gate.respond(req2, "deny")
    assert await fut2 is False


async def test_respond_always_adds_rule(tmp_path: Path) -> None:
    reg = build_registry()
    gate = ApprovalGate(mode="suggest")
    req = "r3"
    fut = asyncio.get_running_loop().create_task(gate.request(req, timeout=10, tool_name="run_shell"))
    await asyncio.sleep(0.01)
    assert gate.respond(req, "always", tool_name="run_shell") is True
    assert await fut is True
    assert gate.decide(reg["run_shell"], {"command": "ls"}, tmp_path, [])[0] == "allow"
