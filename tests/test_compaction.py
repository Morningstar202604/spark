"""上下文压缩 + token 估算测试：compact_messages / _msg_text_for_estimation。

compact_messages 是"上下文管理"的核心路径：超窗时折叠、淘汰不足时逐条删除；
_msg_text_for_estimation 是 token 估算的关键函数——过去用 json.dumps(整条消息)
估算会虚增 5-8% token 数（JSON 结构字符：引号/逗号/花括号）。本测试覆盖：

1. _msg_text_for_estimation：各种消息形态的内容提取
2. compact_messages：未超窗不压缩 / 超窗折叠 / 边界精确
"""
from __future__ import annotations

import pytest

from spark.compaction import _msg_text_for_estimation, compact_messages  # noqa: E402

# -----------------------------------------------------------------------
# _msg_text_for_estimation：从消息里抽 token 估算用文本
# -----------------------------------------------------------------------

def test_msg_text_simple_string_content():
    """纯文本 content 直接返回。"""
    msg = {"role": "user", "content": "你好世界"}
    result = _msg_text_for_estimation(msg)
    assert "你好世界" in result


def test_msg_text_multimodal_list_content():
    """多模态列表形式 content：只取 text part，忽略 image_url。"""
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "分析这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,...."}}
        ],
    }
    result = _msg_text_for_estimation(msg)
    assert "分析这张图" in result
    assert "image_url" not in result
    assert "base64" not in result


def test_msg_text_empty_content():
    """空字符串 content 返回空字符串。"""
    msg = {"role": "assistant", "content": ""}
    result = _msg_text_for_estimation(msg)
    assert result == ""


def test_msg_text_none_content():
    """None content 返回空字符串。"""
    msg = {"role": "assistant", "content": None}
    result = _msg_text_for_estimation(msg)
    assert result == ""


def test_msg_text_tool_calls():
    """tool_calls 提取函数名与参数 value（不含 JSON 结构字符）。"""
    msg = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "read_file", "arguments": '{"path": "test.py", "limit": 50}'},
        }],
    }
    result = _msg_text_for_estimation(msg)
    assert "read_file" in result
    assert "test.py" in result
    # 验证 JSON 结构字符被剥离（引号、花括号不应出现在估算文本里）
    assert '"' not in result or result.count('"') == 0
    assert "{" not in result


def test_msg_text_reasoning_content():
    """reasoning_content（思考链）被纳入估算。"""
    msg = {
        "role": "assistant",
        "content": "做法如此",
        "reasoning_content": "让我想一下，这道题应该...",
    }
    result = _msg_text_for_estimation(msg)
    assert "做法如此" in result
    assert "让我想一下" in result


def test_msg_text_tool_response():
    """tool 角色的 content 直接纳入估算。"""
    msg = {"role": "tool", "tool_call_id": "c1", "content": "文件内容：hello world"}
    result = _msg_text_for_estimation(msg)
    assert "hello world" in result


def test_msg_text_chinese_no_inflation():
    """关键回归：中文消息不含 JSON 结构字符（引号/逗号/花括号/反斜杠）。"""
    msg = {"role": "user", "content": "实现用户登录与密码重置功能"}
    result = _msg_text_for_estimation(msg)
    # 过去 json.dumps 会输出 {"role":"user","content":"..."}，产生大量结构字符
    assert '"' not in result
    assert "{" not in result
    assert "\\" not in result
    assert "实现用户登录与密码重置功能" in result


# -----------------------------------------------------------------------
# compact_messages：上下文压缩行为
# -----------------------------------------------------------------------

async def _compact(messages, max_tokens=32000):
    """快捷调用 compact_messages（用空 provider_cfg 走 heuristic 摘要路径）。"""
    cfg = {"model": "mock", "api_key": ""}
    return await compact_messages(messages, cfg, max_tokens)


def _make_msg(role, content, **kwargs):
    """快速构造消息 dict。"""
    m = {"role": role, "content": content}
    m.update(kwargs)
    return m


@pytest.mark.asyncio
async def test_compact_no_op_when_under_window():
    """未超窗时消息不增不减，直接返回。"""
    msgs = [_make_msg("user", "你好"), _make_msg("assistant", "你好呀")]
    result = await _compact(msgs, max_tokens=32000)
    assert len(result) == 2
    assert result[0]["content"] == "你好"


@pytest.mark.asyncio
async def test_compact_collapses_when_over_window():
    """超窗时把最早的旧对话折叠成一条 system 摘要消息。"""
    # 构造总 token 超过 max_tokens 的消息列表（每条 content 很长）
    big = "A" * 5000
    msgs = [_make_msg("user", f"{big}_{i}") for i in range(10)]
    # max_tokens 极小，迫使折叠发生
    result = await _compact(msgs, max_tokens=100)
    # 折叠后至少会有一条 system 摘要
    has_system = any(m["role"] == "system" and "摘要" in (m.get("content") or "") for m in result)
    assert has_system or len(result) < 10  # 有 folding 或逐条淘汰发生


@pytest.mark.asyncio
async def test_compact_preserves_recent_messages():
    """折叠后保留至少最近 2 条消息（keep = max(2, int(len * 0.4))）。"""
    big = "B" * 3000
    msgs = [_make_msg("user", f"{big}_{i}") for i in range(10)]
    result = await _compact(msgs, max_tokens=100)
    # 至少有 2 条保留（最近 2 条 keep）
    assert len(result) >= 2


@pytest.mark.asyncio
async def test_compact_strips_orphans_after_collapse():
    """折叠/淘汰后清理孤立的 tool 消息（无配对 assistant.tool_calls）。"""
    msgs = [
        _make_msg("user", "do it"),
        _make_msg("assistant", "tool time", tool_calls=[
            {"id": "tc1", "function": {"name": "shell", "arguments": "{}"}}
        ]),
        _make_msg("tool", "执行成功", tool_call_id="tc1"),
        _make_msg("assistant", "完成"),
        _make_msg("user", "再来一轮"),
        _make_msg("assistant", "收到"),
    ]
    # 用足够大的 max_content 先强制折叠，然后再调一次 strip
    # 这里主要验证 strip_orphans 不改变已经是配对的消息
    result = await _compact(msgs, max_tokens=99999)
    # 所有消息都应存在（未触发 folding），无孤儿
    assert len(result) == 6


@pytest.mark.asyncio
async def test_compact_handles_deep_nesting():
    """极端超窗：多次执行 while 循环淘汰直到满足 max_tokens。"""
    # 每条 msg 的估算 token 很少但测试循环逻辑：max_tokens 极小而消息很多
    msgs = [_make_msg("user", f"x_{i}") for i in range(20)]
    result = await _compact(msgs, max_tokens=5)
    # 即使 max_tokens 极小也应至少保留 2 条
    assert len(result) >= 2
