"""P1-1: instructor 结构化输出收口 — 单测。

覆盖三种边界：
1. 首轮即合法 — 直接返回 Pydantic 模型，不触发 repair。
2. 首轮非法 / 缺字段 — 校验失败后自动 repair，第二轮合法 → 成功。
3. 多轮失败 — 耗尽 ``max_retries`` 后抛 :class:`StructuredOutputError`。

所有用例都通过 monkeypatch ``litellm.acompletion`` 注入脚本化响应，
用调用次数断言 repair 循环**真实**被触发，而不是仅靠 happy path。
"""
from __future__ import annotations

import litellm
import pytest
from litellm.types.utils import Choices, Message, ModelResponse
from pydantic import BaseModel, Field

from spark.structured import (
    StructuredOutputError,
    complete,
    get_async_client,
)
from spark.structured import _reset_client_cache


class _MergeNote(BaseModel):
    """Mini schema mirroring memory/extractor._ConsolidateResult."""

    content: str = Field(description="合并后的文本")
    importance: float = Field(description="1-10")


def _fake_response(text: str) -> ModelResponse:
    msg = Message(content=text, role="assistant")
    choice = Choices(message=msg, finish_reason="stop")
    return ModelResponse(choices=[choice], model="fake-model")


def _patch_acompletion(monkeypatch, script: list[str]) -> list[dict]:
    """Replace litellm.acompletion with a scripted async fake.

    Returns the list of kwargs each call was made with, so tests can assert
    *how many* times the LLM was actually hit (the repair-loop witness).
    """
    calls: list[dict] = []
    script_iter = iter(script)

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        try:
            text = next(script_iter)
        except StopIteration:  # pragma: no cover - safety net
            text = '{"content": "fallback", "importance": 5.0}'
        return _fake_response(text)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    _reset_client_cache()
    # Sanity: the fresh client must wrap the *patched* function.
    assert get_async_client() is not None
    return calls


@pytest.fixture(autouse=True)
def _clean_client_cache():
    _reset_client_cache()
    yield
    _reset_client_cache()


# ---------------------------------------------------------------------------
# 1. First-round valid
# ---------------------------------------------------------------------------


async def test_first_round_valid_no_repair(monkeypatch):
    calls = _patch_acompletion(
        monkeypatch,
        ['{"content": "merged preference", "importance": 8.0}'],
    )
    result = await complete(
        _MergeNote,
        system="merge these notes",
        user="1. pnpm\n2. yarn",
        model="fake-model",
        max_retries=3,
    )
    assert isinstance(result, _MergeNote)
    assert result.content == "merged preference"
    assert result.importance == 8.0
    # 关键断言：首轮合法 → 只调一次 LLM，repair 循环没有被触发。
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# 2. First-round invalid → repair succeeds
# ---------------------------------------------------------------------------


async def test_invalid_first_round_then_repair(monkeypatch):
    # 第一次缺 content / importance；第二次给完整合法字段。
    calls = _patch_acompletion(
        monkeypatch,
        [
            '{"wrong_field": 1}',  # 缺必填字段 → Pydantic 校验失败
            '{"content": "merged lesson", "importance": 6.5}',
        ],
    )
    result = await complete(
        _MergeNote,
        system="merge these notes",
        user="1. timeout\n2. retry",
        model="fake-model",
        max_retries=3,
    )
    assert result.content == "merged lesson"
    assert result.importance == 6.5
    # 关键断言：repair 循环真实被触发 — 共调用 2 次。
    assert len(calls) == 2
    # 第二次调用的 messages 应该包含第一次的校验错误信息（instructor 的 repair prompt）。
    second_messages = calls[1]["messages"]
    joined = "\n".join(str(m.get("content", "")) for m in second_messages)
    assert "MergeNote" in joined or "validation" in joined.lower() or "content" in joined


# ---------------------------------------------------------------------------
# 3. Multi-failure boundary
# ---------------------------------------------------------------------------


async def test_all_retries_exhausted_raises(monkeypatch):
    # 始终返回缺字段的非法 JSON；max_retries=2 → 初始 1 次 + 2 次 repair = 3 次。
    calls = _patch_acompletion(
        monkeypatch,
        ['{"wrong": true}'] * 5,  # 多放几条，防止超出后 StopIteration 兜底
    )
    with pytest.raises(StructuredOutputError):
        await complete(
            _MergeNote,
            system="merge",
            user="x",
            model="fake-model",
            max_retries=2,
        )
    # 关键断言：耗尽后确实只打了 3 次（1 + 2），不会无限循环。
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# 4. Integration: memory/extractor.consolidate_group falls back gracefully
# ---------------------------------------------------------------------------


async def test_consolidate_group_uses_structured_path(monkeypatch):
    """When structured output succeeds, consolidate_group returns the schema result."""
    from spark.memory import extractor

    calls = _patch_acompletion(
        monkeypatch,
        ['{"content": "use pnpm", "importance": 7.0}'],
    )
    # chat_complete 不应被调用（结构化路径一次成功）。
    async def _never_chat_complete(**kwargs):  # pragma: no cover
        raise AssertionError("chat_complete should not be called on structured success")

    monkeypatch.setattr(extractor, "chat_complete", _never_chat_complete)

    out = await extractor.consolidate_group(
        contents=["prefer pnpm", "always use pnpm"],
        base_url="http://localhost/v1",
        api_key="k",
        model="fake-model",
    )
    assert out == ("use pnpm", 7.0)
    assert len(calls) == 1


async def test_consolidate_group_falls_back_on_structured_failure(monkeypatch):
    """When structured output exhausts retries, consolidate_group falls back to legacy path."""
    from spark.memory import extractor

    # 始终非法 → structured 路径抛 StructuredOutputError
    _patch_acompletion(monkeypatch, ['{"wrong": 1}'] * 5)

    legacy_calls: list[dict] = []

    async def _fake_chat_complete(**kwargs):
        legacy_calls.append(kwargs)
        # 老路径的 JSON 兜底仍能解析
        return '```json\n{"content": "legacy merged", "importance": 5.0}\n```'

    monkeypatch.setattr(extractor, "chat_complete", _fake_chat_complete)

    out = await extractor.consolidate_group(
        contents=["a", "b"],
        base_url="http://localhost/v1",
        api_key="k",
        model="fake-model",
    )
    # 老路径兜底仍然返回结果
    assert out == ("legacy merged", 5.0)
    # 老路径被调用了一次
    assert len(legacy_calls) == 1
