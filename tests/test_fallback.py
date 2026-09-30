"""备用模型故障切换（fallback）测试：主模型失败 → 自动切备用模型。"""

from __future__ import annotations

from pathlib import Path

import pytest

import spark2.loop as loop_mod
from spark2.loop import AgentLoop
from spark2.provider import ProviderError
from spark2.tools import build_registry


async def _run(provider_cfg: dict, msgs: list[dict], monkeypatch: pytest.MonkeyPatch) -> tuple[list[dict], list[str]]:
    calls: list[str] = []

    async def fake_stream(cfg, msgs_, schemas):  # noqa: ANN001
        calls.append(cfg.get("model"))
        if cfg.get("model") == "broken":
            raise ProviderError("上游 503")
        yield {"type": "text", "text": "ok-from-" + str(cfg.get("model"))}
        yield {"type": "usage", "estimated": 10, "finish_reason": "stop"}

    monkeypatch.setattr(loop_mod, "stream_chat", fake_stream)
    loop = AgentLoop(
        registry=build_registry(with_subagent=False),
        provider_cfg=provider_cfg,
        workdir=Path("/tmp"),
    )
    evs = [ev async for ev in loop.stream(msgs)]
    return evs, calls


async def test_fallback_switches_model(monkeypatch: pytest.MonkeyPatch) -> None:
    evs, calls = await _run(
        {"provider": "mock", "model": "broken", "fallback_model": "good", "api_key": ""},
        [{"role": "user", "content": "hi"}],
        monkeypatch,
    )
    types = [e["type"] for e in evs]
    assert "error" not in types, evs
    assert any(e["type"] == "status" and "备用模型" in e["text"] for e in evs)
    assert calls == ["broken", "good"], calls


async def test_no_fallback_keeps_error(monkeypatch: pytest.MonkeyPatch) -> None:
    evs, calls = await _run(
        {"provider": "mock", "model": "broken", "fallback_model": "", "api_key": ""},
        [{"role": "user", "content": "hi"}],
        monkeypatch,
    )
    assert any(e["type"] == "error" for e in evs)
    assert calls == ["broken"], calls


async def test_fallback_same_model_not_recursive(monkeypatch: pytest.MonkeyPatch) -> None:
    evs, calls = await _run(
        {"provider": "mock", "model": "broken", "fallback_model": "broken", "api_key": ""},
        [{"role": "user", "content": "hi"}],
        monkeypatch,
    )
    assert any(e["type"] == "error" for e in evs)
    assert calls == ["broken"], calls  # 同一模型不重复切换，避免死循环
