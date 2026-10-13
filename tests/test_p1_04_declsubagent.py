"""P1-4: 声明式子代理（.claude/agents/*.md，「文件即 subagent」）。

覆盖：frontmatter 解析、非法拒绝、项目/用户两级发现、按声明 spawn
（mock provider，body 注入子代理 system 槽 + tools 白名单）、与既有并行
task 执行器联动。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from spark.agents import (
    AgentFileError,
    DeclaredAgent,
    discover_declared_agents,
    get_declared_agent,
    inject_subagents,
    parse_agent_file,
    subagent_system_prompt,
)
from spark.config import AgentConfig, SparkConfig
from spark.models import ChatDelta, ToolCall
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry


def _write_agent(wd: Path, name: str, body: str, frontmatter: str | None = None) -> Path:
    d = wd / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    if frontmatter is None:
        frontmatter = (
            "---\n"
            f"name: {name}\n"
            "description: test agent\n"
            "when_to_use: testing\n"
            "tools: read_file, grep\n"
            "---\n"
        )
    p = d / f"{name}.md"
    p.write_text(frontmatter + body, encoding="utf-8")
    return p


# ---------- frontmatter 解析 ----------


def test_parse_valid_agent_frontmatter(tmp_path: Path) -> None:
    p = _write_agent(tmp_path, "reader", "You are a code reader.\n")
    agent = parse_agent_file(p)
    assert isinstance(agent, DeclaredAgent)
    assert agent.name == "reader"
    assert agent.description == "test agent"
    assert agent.when_to_use == "testing"
    assert agent.tools == ["read_file", "grep"]
    assert "code reader" in agent.body


def test_parse_rejects_missing_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "plain.md"
    p.write_text("Just prose, no frontmatter", encoding="utf-8")
    with pytest.raises(AgentFileError, match="frontmatter"):
        parse_agent_file(p)


def test_parse_rejects_empty_body(tmp_path: Path) -> None:
    p = _write_agent(tmp_path, "empty", "")
    with pytest.raises(AgentFileError, match="empty body"):
        parse_agent_file(p)


def test_parse_name_falls_back_to_stem(tmp_path: Path) -> None:
    p = _write_agent(tmp_path, "no-name", "body", "---\ndescription: x\n---\n")
    agent = parse_agent_file(p)
    assert agent.name == "no-name"


# ---------- 发现（项目级 + 用户级） ----------


def test_discover_no_agents_dir_returns_empty(tmp_path: Path) -> None:
    assert discover_declared_agents(tmp_path, global_dir=tmp_path / "nope") == []


def test_discover_project_and_global_with_project_winning(tmp_path: Path) -> None:
    _write_agent(tmp_path, "shared", "project body\n")
    g = tmp_path / "global-agents"
    gd = g / ".claude" / "agents"
    gd.mkdir(parents=True)
    (gd / "shared.md").write_text(
        "---\nname: shared\ndescription: global\n---\nglobal body\n"
    )
    (gd / "global-only.md").write_text(
        "---\nname: global-only\ndescription: only global\n---\nbody\n"
    )
    agents = discover_declared_agents(tmp_path, global_dir=gd)
    by_name = {a.name: a for a in agents}
    assert set(by_name) == {"shared", "global-only"}
    assert "project body" in by_name["shared"].body  # 项目级优先


def test_discover_skips_invalid_file_tolerantly(tmp_path: Path) -> None:
    _write_agent(tmp_path, "good", "body\n")
    (tmp_path / ".claude" / "agents" / "broken.md").write_text("no frontmatter at all")
    agents = discover_declared_agents(tmp_path, global_dir=tmp_path / "nope")
    assert [a.name for a in agents] == ["good"]


def test_get_declared_agent_strict_rejects_malformed(tmp_path: Path) -> None:
    # 文件存在但非法（空 body）→ spawn 路径必须报错拒绝，而不是静默 unknown
    _write_agent(tmp_path, "broken", "")
    with pytest.raises(AgentFileError, match="empty body"):
        get_declared_agent(tmp_path, "broken")
    assert get_declared_agent(tmp_path, "nosuch", global_dir=tmp_path / "nope") is None


# ---------- prompt 注入 ----------


def test_inject_subagents_xml_block() -> None:
    agents = [
        DeclaredAgent(
            name="reader",
            description="reads code",
            body="...",
            path=Path("/tmp/reader.md"),
            when_to_use="explore",
        )
    ]
    out = inject_subagents("base prompt", agents)
    assert "<available_subagents>" in out
    assert 'name="reader"' in out
    assert "task" in out and '"agent"' in out
    assert "base prompt" in out


def test_inject_empty_subagents_unchanged() -> None:
    assert inject_subagents("sys", []) == "sys"


# ---------- 工具白名单 ----------


def test_registry_tool_allowlist_filters_schemas(tmp_path: Path) -> None:
    ctx = ToolContext(
        sandbox=WorkdirSandbox(tmp_path, SparkConfig(agent=AgentConfig())),  # type: ignore[arg-type]
        config=SparkConfig(agent=AgentConfig()),  # type: ignore[arg-type]
    )
    reg = ToolRegistry(ctx)
    reg.tool_allowlist = {"read_file", "grep"}
    names = {s["function"]["name"] for s in reg.schemas()}
    assert names == {"read_file", "grep"}


# ---------- spawn 联动（mock provider） ----------


class RecorderProvider:
    """捕获每次 stream 调用的 messages；可脚本化下一次响应，否则直接收尾。"""

    def __init__(self) -> None:
        self.message_sets: list[list] = []
        self._script: list[ChatDelta] | None = None

    def script_next(self, deltas: list[ChatDelta]) -> None:
        self._script = deltas

    async def stream(self, messages, tools, effort=None):  # noqa: ANN001
        self.message_sets.append([m for m in messages])
        script, self._script = self._script, None
        if script:
            for d in script:
                yield d
            return
        yield ChatDelta(type="text", text="final answer")
        yield ChatDelta(type="end")


def _make_loop(tmp_path: Path, provider: RecorderProvider):
    cfg = SparkConfig(
        provider={"name": "mock", "model": "mock"},
        agent=AgentConfig(
            sandbox_mode="workspace", approval="full-auto", max_tool_rounds=10
        ),
    )
    store = SessionStore(":memory:")
    sid = store.create_session(tmp_path, "mock", title="t")
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, cfg), config=cfg)
    from spark.core.loop import AgentLoop

    loop = AgentLoop(
        workdir=tmp_path, cfg=cfg, provider=provider,  # type: ignore[arg-type]
        registry=ToolRegistry(ctx), store=store, session_id=sid,
    )
    return loop, store


def _system_sets_with_wrapper(provider: RecorderProvider, name: str) -> list[list]:
    needle = f'declared_subagent name="{name}"'
    return [
        ms
        for ms in provider.message_sets
        if any(m.role == "system" and needle in (m.content or "") for m in ms)
    ]


def test_spawn_declared_agent_injects_system_slot(tmp_path: Path) -> None:
    _write_agent(tmp_path, "reader", "You are a read-only code explorer.\n")
    agent = get_declared_agent(tmp_path, "reader", global_dir=tmp_path / "nope")
    assert agent is not None

    async def run() -> None:
        provider = RecorderProvider()
        loop, store = _make_loop(tmp_path, provider)
        async for _ev in loop.spawn_subtask(
            "summarize src",
            system_extra=subagent_system_prompt(agent),  # type: ignore[arg-type]
            tools_allowlist=["read_file", "grep"],
        ):
            pass
        store.close()
        # 子回路的 system 槽必须包含声明 body
        hits = _system_sets_with_wrapper(provider, "reader")
        assert hits, "declared agent body must land in sub-loop system prompt"
        assert any(
            "read-only code explorer" in (m.content or "")
            for ms in hits
            for m in ms
            if m.role == "system"
        )

    asyncio.new_event_loop().run_until_complete(run())


def test_parallel_declared_subagents_linkage(tmp_path: Path) -> None:
    _write_agent(tmp_path, "alpha", "Role ALPHA marker.\n")
    _write_agent(tmp_path, "beta", "Role BETA marker.\n")
    alpha = get_declared_agent(tmp_path, "alpha", global_dir=tmp_path / "nope")
    beta = get_declared_agent(tmp_path, "beta", global_dir=tmp_path / "nope")
    assert alpha and beta

    async def run() -> tuple[int, int, int]:
        provider = RecorderProvider()
        loop, store = _make_loop(tmp_path, provider)
        ends = 0
        async for ev in loop.spawn_subtasks_parallel(
            ["job A", "job B"],
            system_extras=[
                subagent_system_prompt(alpha),  # type: ignore[arg-type]
                subagent_system_prompt(beta),  # type: ignore[arg-type]
            ],
            tools_allowlists=[["read_file"], ["grep"]],
        ):
            if ev.type == "turn_end":
                ends += 1
        alpha_hits = len(_system_sets_with_wrapper(provider, "alpha"))
        beta_hits = len(_system_sets_with_wrapper(provider, "beta"))
        store.close()
        return ends, alpha_hits, beta_hits

    ends, alpha_hits, beta_hits = asyncio.new_event_loop().run_until_complete(run())
    # 复用既有并行执行器：两个子代理各产出一条 turn_end
    assert ends == 2
    assert alpha_hits and beta_hits


def test_task_tool_unknown_agent_returns_error(tmp_path: Path) -> None:
    async def run() -> str:
        provider = RecorderProvider()
        loop, store = _make_loop(tmp_path, provider)
        provider.script_next([
            ChatDelta(type="tool_call", tool_call=ToolCall(
                id="c1", name="task",
                arguments={"agent": "nosuch", "prompt": "hello"})),
            ChatDelta(type="end"),
        ])
        err = ""
        async for ev in loop.iter_turn("do something"):
            if ev.type == "tool_end" and ev.tool_call and ev.tool_call.name == "task":
                assert ev.result is not None
                if not ev.result.ok:
                    err = str(ev.result.payload.get("error", ""))
        store.close()
        return err

    err = asyncio.new_event_loop().run_until_complete(run())
    assert "unknown declared agent" in err


def test_task_tool_declared_agent_e2e(tmp_path: Path) -> None:
    _write_agent(tmp_path, "reader", "You are a read-only code explorer.\n")

    async def run() -> tuple[str, int]:
        provider = RecorderProvider()
        loop, store = _make_loop(tmp_path, provider)
        provider.script_next([
            ChatDelta(type="tool_call", tool_call=ToolCall(
                id="c1", name="task",
                arguments={"agent": "reader", "prompt": "summarize code"})),
            ChatDelta(type="end"),
        ])
        summary = ""
        async for ev in loop.iter_turn("delegate"):
            if ev.type == "tool_end" and ev.tool_call and ev.tool_call.name == "task":
                assert ev.result is not None and ev.result.ok
                summary = str(ev.result.payload.get("summary", ""))
        hits = len(_system_sets_with_wrapper(provider, "reader"))
        store.close()
        return summary, hits

    summary, hits = asyncio.new_event_loop().run_until_complete(run())
    assert summary  # 子代理 final answer 经 task 工具回传
    assert hits, "declared agent must have been spawned with its system body"
