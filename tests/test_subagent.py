"""子 Agent（spawn_subagent）测试：explore 只读 / general 审批 / 嵌套禁止 / 取消传播。"""
from __future__ import annotations

from pathlib import Path

from spark.approval import ApprovalGate
from spark.loop import AgentLoop
from spark.subagent import make_subagent
from spark.tools import build_readonly_registry, build_registry

SPAWN_EXPLORE = [
    {
        "type": "tool_calls",
        "calls": [
            {"id": "sp1", "name": "spawn_subagent", "arguments": {"agent_type": "explore", "task": "看看 lib.py 结构"}}
        ],
    }
]
SUB_READ = [
    {"type": "tool_calls", "calls": [{"id": "s1", "name": "read_file", "arguments": {"path": "lib.py"}}]}
]
SUB_TEXT = [{"type": "text", "text": "lib.py 定义了 add 和 sub 两个函数。"}]
SPAWN_GENERAL = [
    {
        "type": "tool_calls",
        "calls": [
            {"id": "sp2", "name": "spawn_subagent", "arguments": {"agent_type": "general", "task": "写一个 note.txt"}}
        ],
    }
]
SUB_WRITE = [
    {
        "type": "tool_calls",
        "calls": [{"id": "s2", "name": "write_file", "arguments": {"path": "note.txt", "content": "note"}}],
    }
]


def _mk_cfg(*rounds: list, mock_subagent_script: tuple | None = None) -> dict:
    cfg: dict = {"model": "mock", "mock_script": [list(r) for r in rounds]}
    if mock_subagent_script:
        cfg["mock_subagent_script"] = [list(r) for r in mock_subagent_script]
    return cfg


async def _collect(loop: AgentLoop, messages: list[dict]) -> list[dict]:
    out = []
    async for ev in loop.stream(messages):
        out.append(ev)
    return out


async def test_explore_readonly_and_summary(tmp_path: Path) -> None:
    """explore 子 Agent：主 Agent 派只读调查，子 Agent 只能读，结论回传主 Agent。"""
    (tmp_path / "lib.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    loop = AgentLoop(
        tmp_path,
        _mk_cfg(SPAWN_EXPLORE, mock_subagent_script=(SUB_READ, SUB_TEXT)),
        ApprovalGate(mode="full-auto"),
    )
    messages = [{"role": "user", "content": "帮我调查 lib.py"}]
    evs = await _collect(loop, messages)
    types = [e["type"] for e in evs]
    assert "tool_start" in types  # 子 Agent 的 read_file 卡透传
    trs = [e for e in evs if e["type"] == "tool_result"]
    spawn_tr = [e for e in trs if e.get("name") == "spawn_subagent"][-1]
    assert "add" in spawn_tr["output"] and "sub" in spawn_tr["output"]
    # 子 Agent 的 read_file 卡也要有输出
    read_tr = [e for e in trs if e.get("name") == "read_file"]
    assert read_tr, "子 Agent 的 read_file 事件应透传"
    assert evs[-1]["type"] == "done"


async def test_explore_registry_is_readonly() -> None:
    reg = build_readonly_registry()
    names = set(reg)
    assert "write_file" not in names and "run_shell" not in names and "apply_patch" not in names
    assert "spawn_subagent" not in names  # 递归禁止
    assert {"read_file", "search", "glob", "list_dir"} <= names


async def test_general_registry_has_no_subagent() -> None:
    reg = build_registry(with_subagent=False)
    assert "spawn_subagent" not in reg
    assert "write_file" in reg and "run_shell" in reg


async def test_general_subagent_write_goes_through_gate(tmp_path: Path) -> None:
    """general 子 Agent 写文件：必须触发审批，用户允许后才落地（共用审批门）。"""
    loop = AgentLoop(
        tmp_path,
        _mk_cfg(SPAWN_GENERAL, mock_subagent_script=(SUB_WRITE,)),
        ApprovalGate(mode="suggest"),
    )
    messages = [{"role": "user", "content": "让子任务写个文件"}]
    collected = []

    async def drive():
        async for ev in loop.stream(messages):
            collected.append(ev)
            if ev["type"] == "approval":
                approved = loop.gate.respond(ev["request_id"], "allow", ev.get("tool"))
                assert approved

    await drive()
    types = [e["type"] for e in collected]
    assert "approval" in types  # 子 Agent 的写操作弹了审批
    assert (tmp_path / "note.txt").exists()  # 允许后落地
    spawn_tr = [e for e in collected if e["type"] == "tool_result" and e.get("name") == "spawn_subagent"][-1]
    assert spawn_tr["approved"] is True


async def test_subagent_cancel_propagates(tmp_path: Path) -> None:
    """取消传播：主 loop 的 cancel_event 置位后，子 Agent 立即终止（共享取消）。"""
    gate = ApprovalGate(mode="full-auto")
    loop = AgentLoop(tmp_path, _mk_cfg(SPAWN_EXPLORE), gate)
    loop.cancel_event.set()  # 预先取消
    messages = [{"role": "user", "content": "派子任务"}]
    evs = await _collect(loop, messages)
    # 主 loop 取消后直接 done(cancelled)
    assert evs[-1]["type"] == "done" and evs[-1]["reason"] == "cancelled"


async def test_make_subagent_system_prompt_has_role(tmp_path: Path) -> None:
    gate = ApprovalGate()
    sub = make_subagent("explore", tmp_path, _mk_cfg(), gate, None, None, None)
    sp = sub.system_prompt()
    assert "只读调查员" in sp
    sub2 = make_subagent("general", tmp_path, _mk_cfg(), gate, None, None, None)
    assert "通用执行器" in sub2.system_prompt()


# ---------- explore_parallel（并行只读探索） ----------

PARALLEL_SCRIPT = [
    {
        "type": "tool_calls",
        "calls": [
            {
                "id": "ep1",
                "name": "explore_parallel",
                "arguments": {
                    "topics": [
                        {"path": "src/a", "task": "查 a 模块职责"},
                        {"path": "src/b", "task": "查 b 模块职责"},
                    ]
                },
            }
        ],
    }
]
SUB_READ_A = [
    {"type": "tool_calls", "calls": [{"id": "sa", "name": "list_dir", "arguments": {"path": "src/a"}}]}
]
SUB_TEXT_A = [{"type": "text", "text": "a 模块是入口，负责路由。"}]
SUB_READ_B = [
    {"type": "tool_calls", "calls": [{"id": "sb", "name": "list_dir", "arguments": {"path": "src/b"}}]}
]
SUB_TEXT_B = [{"type": "text", "text": "b 模块是数据层，负责存取。"}]


async def test_explore_parallel_merges_results(tmp_path: Path) -> None:
    """explore_parallel：两个只读子 Agent 并行探索，结论按区域合并回传。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a").mkdir()
    (tmp_path / "src" / "b").mkdir()
    cfg = _mk_cfg(
        PARALLEL_SCRIPT,
        # 嵌套模式：[[子Agent0 轮次], [子Agent1 轮次]] → 按 index 分配各自脚本
        mock_subagent_script=[[SUB_READ_A, SUB_TEXT_A], [SUB_READ_B, SUB_TEXT_B]],
    )
    loop = AgentLoop(
        tmp_path,
        cfg,
        gate=ApprovalGate(mode="auto"),
    )
    evs = await _collect(loop, [{"role": "user", "content": "并行探索两个模块"}])
    results = [e for e in evs if e["type"] == "tool_result" and e["name"] == "explore_parallel"]
    assert results, "缺少 explore_parallel 的 tool_result"
    out = results[0]["output"]
    assert "src/a" in out and "src/b" in out
    assert "路由" in out and "数据层" in out
    # 两个子 Agent 各执行了工具步
    steps = [e for e in evs if e["type"] == "tool_start" and e["name"] == "explore_parallel"]
    assert steps and "并行探索" in steps[0]["args_summary"]


async def test_explore_parallel_requires_two_topics(tmp_path: Path) -> None:
    """explore_parallel：少于 2 个主题直接报错，不派任何子 Agent。"""
    cfg = _mk_cfg(
        [
            {
                "type": "tool_calls",
                "calls": [
                    {
                        "id": "ep2",
                        "name": "explore_parallel",
                        "arguments": {"topics": [{"path": "src", "task": "只有一个"}]},
                    }
                ],
            }
        ]
    )
    loop = AgentLoop(tmp_path, cfg, gate=ApprovalGate(mode="auto"))
    evs = await _collect(loop, [{"role": "user", "content": "并行探索"}])
    results = [e for e in evs if e["type"] == "tool_result" and e["name"] == "explore_parallel"]
    assert results and "至少 2 个" in results[0]["output"]
