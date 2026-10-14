"""系统提示词缓存（性能）：命中不重扫仓库，写操作后失效重算。"""

from __future__ import annotations

from pathlib import Path

from spark.config import SparkConfig
from spark.core import context as ctx


def _counting_repo_map(monkeypatch) -> dict:
    import spark.repo_map as rm

    calls = {"n": 0}
    orig = rm.build_repo_map

    def counting(*args, **kwargs):
        calls["n"] += 1
        return orig(*args, **kwargs)

    monkeypatch.setattr(rm, "build_repo_map", counting)
    return calls


def test_system_prompt_cache_hit_and_invalidate(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    cfg = SparkConfig()
    cfg.agent.system_prompt = "BASE-PROMPT"
    calls = _counting_repo_map(monkeypatch)

    ctx.invalidate_system_prompt_cache()
    first = ctx.system_prompt(tmp_path, cfg)
    second = ctx.system_prompt(tmp_path, cfg)
    assert first == second
    assert calls["n"] == 1, "第二次调用必须命中缓存，不得重扫仓库"

    ctx.invalidate_system_prompt_cache()
    ctx.system_prompt(tmp_path, cfg)
    assert calls["n"] == 2, "失效后必须重算"


def test_system_prompt_cache_key_reacts_to_config(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    cfg = SparkConfig()
    cfg.agent.system_prompt = "BASE-PROMPT"
    calls = _counting_repo_map(monkeypatch)

    ctx.invalidate_system_prompt_cache()
    ctx.system_prompt(tmp_path, cfg)
    cfg.agent.sandbox_mode = "unrestricted"  # 配置变化 → key 变化 → 重算
    ctx.system_prompt(tmp_path, cfg)
    assert calls["n"] == 2


def test_agent_loop_invalidates_after_write_tool(tmp_path: Path, monkeypatch) -> None:
    """写工具成功后 AgentLoop 必须失效缓存（下轮 system_prompt 重算）。"""
    from spark.core.loop import AgentLoop
    from spark.models import ChatDelta, ToolCall
    from spark.providers.mock import MockProvider
    from spark.sandbox import WorkdirSandbox
    from spark.store import SessionStore
    from spark.tools.registry import ToolContext, ToolRegistry

    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "cache.db")
    sid = store.create_session(tmp_path, "mock")
    ctx_obj = ToolContext(sandbox=WorkdirSandbox(tmp_path), config=cfg)
    provider = MockProvider(
        rounds=[
            [
                ChatDelta(
                    type="tool_call",
                    tool_call=ToolCall(
                        id="c1",
                        name="write_file",
                        arguments={"path": "out.txt", "content": "hi"},
                    ),
                ),
                ChatDelta(type="end"),
            ],
            [ChatDelta(type="text", text="done"), ChatDelta(type="end")],
        ]
    )
    loop = AgentLoop(
        workdir=tmp_path,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx_obj),
        store=store,
        session_id=sid,
    )
    calls = _counting_repo_map(monkeypatch)
    import asyncio

    asyncio.run(loop.run("write a file"))
    assert calls["n"] >= 2, "写工具成功后缓存应失效，system_prompt 至少重算一次"
    store.close()
