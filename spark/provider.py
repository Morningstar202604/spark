"""Provider 层：OpenAI 兼容流式客户端 + 演示 mock + token 估算。

重构：真实模型调用改用官方 OpenAI SDK，保留 mock 与 token 估算。
- SDK 自带 SSE 流式解析、tool_call 增量拼装、429/5xx 重试（可选），省去手写 300 行。
- 接口保持兼容：``stream_chat`` / ``summarize_messages`` / ``estimate_tokens`` / ``ProviderError`` / ``test_connection``。
- mock 提供脚本化事件流，用于测试与无密钥演示。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx  # 保留模块级引用，供测试 monkeypatch 用

import spark.trace as trace

# 公开常量（保留供测试/兼容引用）
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# tokenizer（tiktoken 提为首选，未装时回退启发式）
# ---------------------------------------------------------------------------

_ENCODER: object | None = None
_ENCODER_TRIED = False


def _get_encoder():
    global _ENCODER, _ENCODER_TRIED
    if not _ENCODER_TRIED:
        _ENCODER_TRIED = True
        try:
            import tiktoken  # type: ignore

            _ENCODER = tiktoken.get_encoding("o200k_base")
        except Exception:  # noqa: BLE001
            _ENCODER = None
    return _ENCODER


def estimate_tokens(text: str) -> int:
    """估算 token 数：优先 tiktoken（精确），回退启发式。"""
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    other = len(text) - cjk
    return int(cjk * 1.5 + other / 4) + 8


# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# OpenAI SDK 客户端
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 客户端缓存（避免每次 stream_chat / summarize_messages 新建 httpx 连接池）
# ---------------------------------------------------------------------------

# OpenAI SDK 实例是线程安全、可复用的；按 (base_url, api_key, proxy) 三元组缓存，
# 相同 endpoint + 密钥的调用共享连接池。缓存由模块生命周期持有，
# 进程退出时随 Python GC 回收；httpx 内置的连接池 idle timeout (默认 5s) 会自动关掉空闲连接。
_client_cache: dict[tuple[str, str, str], object] = {}

def _client_cache_key(cfg: dict) -> tuple[str, str, str]:
    """构造 OpenAI 客户端缓存 key：base_url + api_key + proxy 三者决定连接复用条件。"""
    return (
        (cfg.get("base_url") or "").rstrip("/"),
        cfg.get("api_key", ""),
        str(cfg.get("proxy") or "").strip(),
    )


def _make_client(cfg: dict):
    """从配置构建 OpenAI SDK 异步客户端；同配置多次调用复用同一实例（连接池共享）。

    - 使用 AsyncOpenAI（异步原生），消除 sync-over-async 运行时异常
    - 应用层自行按业务语义重试（"无产出才重试"），SDK max_retries=0 避免双层重试
    - 连接池按 (base_url, api_key, proxy) 缓存，密集工具调用时复用
    """
    key = _client_cache_key(cfg)
    client = _client_cache.get(key)
    if client is not None:
        return client

    try:
        from openai import AsyncOpenAI  # type: ignore
    except ImportError as e:
        raise ProviderError(
            "未安装 openai SDK：pip install openai（推荐）或自行实现 Provider。"
        ) from e

    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise ProviderError("未配置模型接口地址")

    client = AsyncOpenAI(
        api_key=cfg.get("api_key", "sk-empty"),
        base_url=base,
        max_retries=0,  # 应用层 stream_chat() 自行按业务语义重试，避免与 SDK 双层重试
        timeout=httpx.Timeout(300, connect=30),
        http_client=httpx.AsyncClient(
            proxy=str(cfg.get("proxy") or "").strip() or None,
        ),
    )
    _client_cache[key] = client
    return client


def clear_client_cache() -> None:
    """清空客户端缓存（测试隔离、热重载配置时调用）。"""
    _client_cache.clear()


# ---------------------------------------------------------------------------
# 可选采样参数
# ---------------------------------------------------------------------------


def _opt_float(v) -> float | None:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_int(v) -> int | None:
    try:
        if v is None or str(v).strip() == "":
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# mock（脚本化事件流，无外网）
# ---------------------------------------------------------------------------


def _msg_text(content) -> str:
    """从 OpenAI 兼容 content（str 或 [{type:text|image_url}]）提取纯文本部分。"""
    if isinstance(content, list):
        return " ".join(
            str(p.get("text") or "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
        ).strip()
    return str(content or "")


async def _mock_chat(
    cfg: dict, messages: list[dict], tools: list[dict] | None
) -> AsyncIterator[dict]:
    """演示/测试用的脚本化 Provider。cfg["mock_script"] 为轮次列表。"""
    script = cfg.get("mock_script")
    if isinstance(script, list) and script:
        batch = script.pop(0)
        for ev in batch:
            yield ev
        if not script:
            yield {"type": "text", "text": "（演示模式）任务已按脚本完成。"}
        yield {
            "type": "usage",
            "estimated": 100,
            "prompt_tokens": 60,
            "completion_tokens": 40,
            "model": cfg.get("model") or "mock",
        }
        return
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            user_text = (
                _msg_text(m["content"])
                if isinstance(m["content"], list)
                else str(m["content"])
            )
            break
    if "列" in user_text and tools:
        yield {
            "type": "tool_calls",
            "calls": [
                {
                    "id": "call_demo_list",
                    "name": "list_dir",
                    "arguments": {"path": "."},
                }
            ],
        }
        yield {
            "type": "usage",
            "estimated": 60,
            "prompt_tokens": 40,
            "completion_tokens": 20,
            "model": cfg.get("model") or "mock",
        }
        return
    yield {
        "type": "text",
        "text": "（演示模式）你好，我是 Spark 重建版。配置真实模型后即可使用。",
    }
    yield {
        "type": "usage",
        "estimated": 40,
        "prompt_tokens": 25,
        "completion_tokens": 15,
        "model": cfg.get("model") or "mock",
    }


# ---------------------------------------------------------------------------
# 真实模型调用（OpenAI SDK）
# ---------------------------------------------------------------------------


async def stream_chat(
    cfg: dict,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_delta: int | None = None,
) -> AsyncIterator[dict]:
    """流式调用 OpenAI 兼容 /chat/completions。

    产出事件：
      {"type": "text", "text": str}
      {"type": "reasoning", "text": str}
      {"type": "tool_calls", "calls": [{"id", "name", "arguments": dict}]}
      {"type": "usage", "estimated": int}
    """
    if cfg.get("model") == "mock":
        async for ev in _mock_chat(cfg, messages, tools):
            yield ev
        return

    input_est = sum(estimate_tokens(str(m)) for m in messages)
    if tools:
        input_est += sum(estimate_tokens(str(t)) for t in tools)

    params: dict = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "stream": True,
    }
    temp = _opt_float(cfg.get("temperature"))
    if temp is not None:
        params["temperature"] = temp
    mt = _opt_int(cfg.get("max_tokens"))
    if mt is not None:
        params["max_tokens"] = mt
    if tools:
        params["tools"] = tools

    out_tokens = 0
    client = _make_client(cfg)

    # 应用层重试：只重试"未产出任何内容前的瞬态错误"（5xx/429/连接错误）；
    # 已流出一部分内容后失败不重试（避免重复输出）。SDK 侧 max_retries=0 避免双层重试。
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        out_tokens = 0
        try:
            stream = await client.chat.completions.create(**params)
            async for chunk in stream:
                choices = chunk.choices or []
                if not choices:
                    continue
                ch = choices[0]
                delta = ch.delta
                if delta is None:
                    continue
                content = getattr(delta, "content", None)
                if content:
                    text = content
                    out_tokens += estimate_tokens(text)
                    yield {"type": "text", "text": text}
                    if max_delta is not None and out_tokens > max_delta:
                        return
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                if delta.tool_calls:
                    calls: list[dict] = []
                    for tc in delta.tool_calls:
                        args_raw = tc.function.arguments or "{}"
                        try:
                            parsed = json.loads(args_raw)
                        except json.JSONDecodeError:
                            parsed = None
                        if not isinstance(parsed, dict):
                            calls.append(
                                {
                                    "id": tc.id or "",
                                    "name": tc.function.name or "",
                                    "arguments": {},
                                    "invalid_arguments": True,
                                    "arguments_tail": args_raw[-200:],
                                }
                            )
                        else:
                            calls.append(
                                {
                                    "id": getattr(tc, "id", "") or "",
                                    "name": getattr(getattr(tc, "function", None), "name", "") or "",
                                    "arguments": parsed,
                                }
                            )
                    if calls:
                        yield {"type": "tool_calls", "calls": calls}
                usage = getattr(chunk, "usage", None)
                if usage:
                    yield {
                        "type": "usage",
                        "estimated": input_est + out_tokens,
                        "prompt_tokens": int(usage.prompt_tokens or input_est),
                        "completion_tokens": int(usage.completion_tokens or out_tokens),
                        "model": chunk.model or cfg.get("model") or "unknown",
                        "finish_reason": getattr(ch, "finish_reason", None),
                    }
                    return
            # 无 usage chunk：走本地估算
            yield {
                "type": "usage",
                "estimated": input_est + out_tokens,
                "prompt_tokens": input_est,
                "completion_tokens": out_tokens,
                "model": cfg.get("model") or "unknown",
                "finish_reason": None,
            }
            return
        except Exception as e:
            status = getattr(e, "status_code", None)
            retryable = status is None or status in (429, 500, 502, 503, 504)
            if not retryable:
                raise ProviderError(str(e), status=status) from e
            last_err = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(0.5 * (2 ** attempt))
    raise ProviderError(str(last_err), status=getattr(last_err, "status_code", None))


# ---------------------------------------------------------------------------
# 消息摘要（上下文压缩用）
# ---------------------------------------------------------------------------


def _heuristic_summary(messages: list[dict], max_chars: int = 1200) -> str:
    """本地启发式摘要：提取关键信息（文件路径、命令、决定）+ 截断拼接。
    纯字符串操作，零 LLM 调用。"""
    import re

    parts: list[str] = []
    file_paths: list[str] = []
    commands: list[str] = []
    decisions: list[str] = []

    for m in messages[:80]:
        content = str(m.get("content") or "")
        # 提取文件路径
        for fp in re.findall(r'[\w./\-_]+\.(?:py|js|ts|go|rs|java|cpp|c|h|toml|json|yaml|yml|md)', content):
            if fp not in file_paths:
                file_paths.append(fp)
        # 提取 shell 命令
        for cmd in re.findall(r'(?:运行|执行|命令|run|execute)[：:]\s*(.+)', content):
            if cmd not in commands:
                commands.append(cmd.strip()[:80])
        # 提取决定/结论
        for line in content.split("\n"):
            if any(kw in line for kw in ("决定", "结论", "应该", "改为", "使用", "采用", "fixed", "decided")):
                stripped = line.strip()
                if stripped and stripped not in decisions:
                    decisions.append(stripped[:100])

        role = m.get("role")
        tag = {"user": "用户", "assistant": "助手", "tool": "工具"}.get(str(role), str(role))
        text = content.replace("\n", " ").strip()
        if m.get("tool_calls"):
            text = (text + " [调用工具]").strip()
        if text:
            parts.append(f"{tag}：{text[:100]}")

    summary_parts: list[str] = []
    if file_paths:
        summary_parts.append("涉及文件：" + "、".join(file_paths[:10]))
    if commands:
        summary_parts.append("执行命令：" + "、".join(commands[:6]))
    if decisions:
        summary_parts.append("关键结论：" + "；".join(decisions[:6]))
    if parts:
        summary_parts.append("对话摘要：" + "；".join(parts[:15]))

    text = "\n".join(summary_parts)
    return text[:max_chars] if text else "（早期对话摘要）"


async def summarize_messages(cfg: dict, messages: list[dict]) -> str:
    """把一段旧对话压缩成要点摘要（用于上下文满窗时腾空间）。

    默认纯本地（启发式），绝不调用 LLM —— 保持"零隐性 LLM 调用"承诺。
    需要更高质量摘要可在 config 中设置 compact_summary = "llm"（opt-in）。
    """
    summary = _heuristic_summary(messages)

    if cfg.get("compact_summary") == "llm" and cfg.get("model") != "mock" and cfg.get("api_key"):
        try:
            client = _make_client(cfg)
            compact_text = _heuristic_summary(messages, max_chars=6000)
            resp = await client.chat.completions.create(
                model=cfg.get("model") or "deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": "把下面的早期对话压缩成 200 字以内的中文要点摘要，保留：关键决定、涉及的文件路径、执行的命令、重要结论与未完成事项。只输出摘要本身。",
                    },
                    {"role": "user", "content": compact_text},
                ],
                stream=False,
                max_tokens=300,
                timeout=15.0,
            )
            content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
            if content:
                return "（LLM 摘要）" + content[:1500]
        except Exception as exc:  # noqa: BLE001 —— 摘要失败不阻断对话，退回启发式
            trace.warning("compact_llm_summary_failed", error=str(exc))

    return "（早期对话摘要）" + summary


# ---------------------------------------------------------------------------
# 连通性测试
# ---------------------------------------------------------------------------


async def test_connection(cfg: dict) -> tuple[bool, str]:
    """最小连通性测试：发一条普通消息，看是否能拿到响应。"""
    if cfg.get("model") == "mock":
        return True, "演示模式已就绪"
    if not cfg.get("api_key"):
        return False, "缺少 API Key"
    try:
        async for _ in stream_chat(
            cfg, [{"role": "user", "content": "hi"}], max_delta=80
        ):
            pass
        return True, "连接成功"
    except ProviderError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"连接失败: {e}"
