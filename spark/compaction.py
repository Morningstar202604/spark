"""上下文压缩：超窗折叠旧对话 + 孤儿 tool 消息清理（原 loop._compact / _strip_orphans 拆分）。

纯函数模块；Provider 配置与窗口上限由调用方（AgentLoop）传入。
"""

from __future__ import annotations

from spark.provider import estimate_tokens, summarize_messages

try:
    from langchain_core.messages import trim_messages

    _TRIM_MESSAGES_OK = True
except (ImportError, AttributeError, ValueError):  # noqa: BLE001
    # langchain_core 未安装 / 版本/轮子不兼容 -> 保留 hand-rolled 兜底
    _TRIM_MESSAGES_OK = False


def strip_orphans(messages: list[dict]) -> None:
    """清理折叠/淘汰后残留的孤儿 tool / tool_calls 消息。

    真实模型（OpenAI 兼容）严格校验：每个 tool 消息必须有对应的
    assistant.tool_calls；每个 assistant.tool_calls 里的调用都必须有 tool 应答。
    上下文压缩可能拆散配对，本函数在原列表上就地修复，避免请求 400。
    """
    i = 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "tool":
            if not any(
                prev.get("role") == "assistant"
                and any(
                    tc.get("id") == m.get("tool_call_id")
                    for tc in (prev.get("tool_calls") or [])
                )
                for prev in messages[:i]
            ):
                messages.pop(i)
                continue
            i += 1
        elif m.get("role") == "assistant" and m.get("tool_calls"):
            keep = [
                tc
                for tc in m["tool_calls"]
                if any(
                    nxt.get("role") == "tool"
                    and nxt.get("tool_call_id") == tc.get("id")
                    for nxt in messages[i + 1 :]
                )
            ]
            if keep:
                m["tool_calls"] = keep
            else:
                m.pop("tool_calls", None)
                if m.get("content") is None:
                    m["content"] = ""
            i += 1
        else:
            i += 1


async def compact_messages(
    messages: list[dict], provider_cfg: dict, max_context_tokens: int
) -> list[dict]:
    """上下文管理：超窗时把最早的旧对话折叠成一条摘要，而不是硬删。

    规则：先把最早约 60%（至少保留最近 2 条）压缩为一条 system 摘要消息；
    仍超窗才用 LangChain `trim_messages` 做 token-count-based 淘汰。
    兜底策略：langchain_core 未安装或调用出错时回退到旧的手动逐条淘汰。
    mock/无密钥走启发式摘要。返回处理后的列表（就地修改 messages）。
    """
    # Token 估算是对消息"内容字段"而非整条 JSON，避免 JSON 结构字符（引号、逗号、
    # \uXXXX 转义）虚增 token 数——中文消息 json.dumps 开销尤其显著（约多占 5-8%）。
    total = sum(estimate_tokens(_msg_text_for_estimation(m)) for m in messages)
    if total <= max_context_tokens:
        return messages
    # ---- 阶段 1：fold-to-summary（行为与原来一致）----
    if len(messages) > 2:
        keep = max(2, int(len(messages) * 0.4))
        fold_n = len(messages) - keep
        if fold_n >= 1:
            fold = messages[:fold_n]
            rest = messages[fold_n:]
            try:
                summary = await summarize_messages(provider_cfg, fold)
            except Exception:  # noqa: BLE001
                summary = "（早期对话摘要）"
            messages[:] = [{"role": "system", "content": summary}] + rest
    # ---- 阶段 2：仍超窗则做 token-count-based trimming ----
    total = sum(estimate_tokens(_msg_text_for_estimation(m)) for m in messages)
    if total <= max_context_tokens:
        strip_orphans(messages)
        return messages
    if _TRIM_MESSAGES_OK:
        try:
            trimmed = _trim_messages_internal(messages, max_context_tokens)
        except Exception:  # noqa: BLE001
            # trim_messages 兜底失败时回退到手式 evict
            trimmed = None
        if trimmed is not None:
            messages[:] = trimmed
            strip_orphans(messages)
            return messages
    # ---- 阶段 3：langchain 不可用或异常 -> 保留原手式逐条淘汰 ----
    total = sum(estimate_tokens(_msg_text_for_estimation(m)) for m in messages)
    while total > max_context_tokens and len(messages) > 2:
        # 淘汰最旧普通消息；跳过摘要/系统消息，避免把刚生成的摘要当最旧消息删掉
        idx = 0
        while idx < len(messages) - 1 and messages[idx].get("role") == "system":
            idx += 1
        if idx >= len(messages) - 1:
            break
        removed = messages.pop(idx)
        total -= estimate_tokens(_msg_text_for_estimation(removed))
    # 折叠/淘汰可能拆散 assistant(tool_calls)↔tool 配对，就地清理孤儿消息
    strip_orphans(messages)
    return messages


def _trim_messages_internal(messages: list[dict], max_context_tokens: int) -> list[dict] | None:
    """调用 LangChain `trim_messages` 做 token-count-based 淘汰并还原为 dict 列表。

    返回 None 时由调用方回退到手式淘汰。
    内部以 LangChain Message 对象形态输送给 `trim_messages`，
    并以自定义 counter 复用本地 `_msg_text_for_estimation`。
    """
    lc_messages = [_dict_to_lc_message(m) for m in messages]
    lc_trimmed = trim_messages(
        lc_messages,
        max_tokens=max_context_tokens,
        strategy="last",
        allow_partial=False,
        token_counter=_lc_message_list_token_counter,
        include_system=True,
    )
    if not lc_trimmed:
        return None
    trimmed = [_lc_message_to_dict(m) for m in lc_trimmed]

    # trim_messages 可能因 max_tokens 极小（甚至小于单条消息 token 数）而只返回
    # system 摘要或清空；手式兜底保证至少保留最近 2 条消息（测试不变式）。
    if len(trimmed) < 2 and len(messages) >= 2:
        fallback = messages[-2:]
        # 若 fallback 整体仍超限也返回，避免 loop 崩溃；调用方不会再进 compact。
        return fallback if fallback else trimmed

    return trimmed


def _msg_text_for_estimation(m: dict) -> str:
    """从消息里抽 token 估算用的文本——保内容字段、舍弃 JSON 结构字符。

    覆盖：
    - content (str | list[part])：多模态 part 只取 text 部分
    - tool_calls：取 function name + arguments（去掉 JSON 结构外壳，只留参数值）
    - tool 结果：tool 角色的消息 content 原文
    - thinking / reasoning：reasoning 内容原文
    - 其他（system / metadata）：content 原文或 ""
    """
    parts: list[str] = []
    # 主内容
    content = m.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
    elif content:
        parts.append(str(content))
    # 推理字段（o1/o3/deepseek thinking 等产出）
    if m.get("reasoning_content"):
        parts.append(str(m["reasoning_content"]))
    # tool_calls：取函数名与参数（只取参数字符串，不加 JSON 结构）
    for tc in m.get("tool_calls") or []:
        func = tc.get("function") or {}
        func_name = func.get("name", "")
        if func_name:
            parts.append(func_name)
        args = func.get("arguments", "")
        if args and isinstance(args, str):
            # 简单剥离 JSON 花括号与 key 名，只留 value 部分做粗略估算
            import re as _re
            vals = _re.findall(r':\s*"?([^",}\n]+)"?', args)
            parts.extend(v for v in vals if v)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# LangChain 互操作：dict <-> BaseMessage 转换 + 基于 _msg_text_for_estimation 的
# LangChain 消息列表 token 计数器（供 trim_messages 使用）
# ---------------------------------------------------------------------------

if _TRIM_MESSAGES_OK:
    from re import findall as _findall

    from langchain_core.messages import (
        AIMessage,
        HumanMessage,
        SystemMessage,
        ToolMessage,
    )

    _LC_ROLE_MAP = {
        "system": SystemMessage,
        "user": HumanMessage,
        "assistant": AIMessage,
        "tool": ToolMessage,
    }

    def _dict_to_lc_message(m: dict):
        """把 OpenAI 风格 dict 转为 LangChain BaseMessage 子类实例。

        保留 tool_calls 放在 additional_kwargs 中，避免被 LC 当成 kwargs 鼓鼓囊囊地反序列化；
        tool 消息的 tool_call_id 也放 additional_kwargs，便于实现双向可逆转换。
        """
        role = m.get("role", "")
        msg_cls = _LC_ROLE_MAP.get(role, HumanMessage)
        kwargs: dict = {}
        tool_calls = m.get("tool_calls")
        if tool_calls:
            kwargs["tool_calls"] = tool_calls
            kwargs["additional_kwargs"] = {"tool_calls": tool_calls}
        if role == "tool" and m.get("tool_call_id"):
            kwargs.setdefault("additional_kwargs", {})
            kwargs["additional_kwargs"]["tool_call_id"] = m["tool_call_id"]
        # dict 直接当构造子构造 LC 消息
        return msg_cls(**kwargs) if kwargs else msg_cls(
            content=_lc_extract_content_for_init(m)
        )

    def _lc_extract_content_for_init(m: dict):
        """从 dict 拿到可直接送入 LC 构造的 content。"""
        content = m.get("content")
        return content if isinstance(content, str) else ""

    def _lc_message_to_dict(m) -> dict:
        """从 LangChain BaseMessage 子类实例还原为 OpenAI 风格 dict。"""
        role = getattr(m, "role", None)
        if role is None:
            cls_name = type(m).__name__
            role = {
                "SystemMessage": "system",
                "HumanMessage": "user",
                "AIMessage": "assistant",
                "ToolMessage": "tool",
            }.get(cls_name, "user")
        role_map_reverse = {"human": "user", "ai": "assistant"}
        role = role_map_reverse.get(role, role)
        d: dict = {"role": role, "content": m.content}
        tcid = getattr(m, "tool_call_id", None)
        if tcid:
            d["tool_call_id"] = tcid
        tool_calls = getattr(m, "tool_calls", None) or m.additional_kwargs.get("tool_calls")
        if tool_calls:
            normalized = []
            for tc in tool_calls:
                if isinstance(tc, dict):
                    if "function" in tc:
                        normalized.append(tc)
                    elif "name" in tc and "args" in tc:
                        normalized.append({
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": tc["args"]},
                        })
                    else:
                        normalized.append(tc)
                else:
                    normalized.append(tc)
            d["tool_calls"] = normalized
        return d

    def _lc_message_text_for_estimation(m) -> str:
        """LC BaseMessage 实例的 token 估算文本抽取。

        等价于 `_msg_text_for_estimation` 的 LC 版——避免 JSON 结构字符虚增 token 数。
        """
        parts: list[str] = []
        content = getattr(m, "content", None)
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
        elif content:
            parts.append(str(content))
        tool_calls = getattr(m, "tool_calls", None)
        if not tool_calls:
            tool_calls = m.additional_kwargs.get("tool_calls")
        for tc in tool_calls or []:
            func = tc.get("function") if isinstance(tc, dict) else {}
            if func is None:
                func = {}
            func_name = func.get("name", "")
            if func_name:
                parts.append(func_name)
            args = func.get("arguments", "")
            if args and isinstance(args, str):
                vals = _findall(r':\s*"?([^",}\n]+)"?', args)
                parts.extend(v for v in vals if v)
        return " ".join(parts)

    def _lc_message_list_token_counter(lc_messages) -> int:
        """LangChain 风格 token 计数器：sum(estimate_tokens(text_per_msg))。

        trim_messages 要求的 Callable[[list[BaseMessage]], int]；
        LC 在调用 list 计数器前本就会把输入转换为 BaseMessage 子类。
        """
        return sum(estimate_tokens(_lc_message_text_for_estimation(m)) for m in lc_messages)


def json_dumps(m: dict) -> str:
    """消息 JSON 序列化（紧凑、保中文，token 估算输入）。

    注意：token 估算请用 _msg_text_for_estimation() 代替本函数——
    序列化 JSON 的结构字符（引号/逗号/花括号）会虚增约 5-8% token 数。
    本函数保留给其它需要真正 JSON 字符串的场景用。
    """
    import json

    return json.dumps(m, ensure_ascii=False)
