"""上下文压缩：超窗折叠旧对话 + 孤儿 tool 消息清理（原 loop._compact / _strip_orphans 拆分）。

纯函数模块；Provider 配置与窗口上限由调用方（AgentLoop）传入。
"""

from __future__ import annotations

from spark.provider import estimate_tokens, summarize_messages


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
    仍超窗才逐条淘汰最旧消息。压缩只发生在满窗时，mock/无密钥走启发式摘要。
    返回处理后的列表（就地修改 messages）。
    """
    # Token 估算是对消息"内容字段"而非整条 JSON，避免 JSON 结构字符（引号、逗号、
    # \uXXXX 转义）虚增 token 数——中文消息 json.dumps 开销尤其显著（约多占 5-8%）。
    total = sum(estimate_tokens(_msg_text_for_estimation(m)) for m in messages)
    if total <= max_context_tokens:
        return messages
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


def json_dumps(m: dict) -> str:
    """消息 JSON 序列化（紧凑、保中文，token 估算输入）。

    注意：token 估算请用 _msg_text_for_estimation() 代替本函数——
    序列化 JSON 的结构字符（引号/逗号/花括号）会虚增约 5-8% token 数。
    本函数保留给其它需要真正 JSON 字符串的场景用。
    """
    import json

    return json.dumps(m, ensure_ascii=False)
