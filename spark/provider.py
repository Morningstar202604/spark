"""Provider 层：OpenAI 兼容流式客户端 + 演示 mock + token 估算。

重构：真实模型调用改用官方 OpenAI SDK，保留 mock 与 token 估算。
- SDK 自带 SSE 流式解析、tool_call 增量拼装、429/5xx 重试（可选），省去手写 300 行。
- 接口保持兼容：``stream_chat`` / ``summarize_messages`` / ``estimate_tokens`` / ``ProviderError`` / ``test_connection``。
- mock 提供脚本化事件流，用于测试与无密钥演示。
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator

import httpx  # 保留模块级引用，供测试 monkeypatch 用

import spark.trace as trace

# 公开常量
# Retry 配置：指数退避 + jitter 防惊群
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.5  # 秒
RETRY_BACKOFF_FACTOR = 2.0
RETRY_JITTER = 0.5  # 随机抖动幅度
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def _extract_retry_after(exc: Exception) -> float | None:
    """从 OpenAI SDK 异常中提取 Retry-After 值（秒）。

    OpenAI SDK 在 429 异常里通过 .headers 透传 Retry-After。
    读取路径兼容 attrs 与 dict 两种风格；失败返回 None 走默认退避。
    """
    # 路径 1：直接挂在异常上的 .headers
    headers = getattr(exc, "headers", None)
    if headers is not None:
        if hasattr(headers, "get"):
            ra = headers.get("Retry-After") or headers.get("retry-after")
        else:
            ra = getattr(headers, "Retry-After", None) or getattr(headers, "retry-after", None)
        if ra:
            try:
                return float(ra)
            except (ValueError, TypeError):
                pass
    # 路径 2：OpenAI SDK 把原始 response 挂在 .response 上，response.headers 是真实来源
    resp = getattr(exc, "response", None)
    if resp is not None:
        resp_headers = getattr(resp, "headers", None)
        if resp_headers is not None:
            if hasattr(resp_headers, "get"):
                ra = resp_headers.get("Retry-After") or resp_headers.get("retry-after")
            else:
                ra = getattr(resp_headers, "Retry-After", None) or getattr(resp_headers, "retry-after", None)
            if ra:
                try:
                    return float(ra)
                except (ValueError, TypeError):
                    pass
    return None

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
    """从配置构建 OpenAI SDK 客户端；同配置多次调用复用同一实例（连接池共享）。

    过去每次调用都 new 一个 OpenAI → 新 httpx.AsyncClient → 新连接池，
    工具循环密集调用时大量 TIME_WAIT 与 TLS 手豉；现按 (base_url, api_key, proxy) 缓存客户端。
    """
    key = _client_cache_key(cfg)
    client = _client_cache.get(key)
    if client is not None:
        return client

    try:
        from openai import OpenAI  # type: ignore
    except ImportError as e:
        raise ProviderError(
            "未安装 openai SDK：pip install openai（推荐）或自行实现 Provider。"
        ) from e

    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise ProviderError("未配置模型接口地址")

    client = OpenAI(
        api_key=cfg.get("api_key", "sk-empty"),
        base_url=base,
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(300, connect=30),
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
    produced = False
    last_err: Exception | None = None
    client = _make_client(cfg)

    for attempt in range(MAX_RETRIES):
        produced = False
        try:
            stream = await client.chat.completions.create(**params)
            async for chunk in stream:
                choices = chunk.choices or []
                if not choices:
                    continue
                ch = choices[0]
                delta = ch.delta
                if delta and delta.content:
                    text = delta.content
                    produced = True
                    out_tokens += estimate_tokens(text)
                    yield {"type": "text", "text": text}
                    if max_delta is not None and out_tokens > max_delta:
                        return
                if delta and delta.reasoning_content:
                    produced = True
                    yield {"type": "reasoning", "text": delta.reasoning_content}
                if delta and delta.tool_calls:
                    produced = True
                    calls = []
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
                                    "id": tc.id or "",
                                    "name": tc.function.name or "",
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
            retryable = status is None or status in RETRYABLE_STATUS_CODES
            if produced or not retryable:
                raise ProviderError(str(e), status=status) from e
            last_err = e
            # Retry-After 优先，否则指数退避 + jitter
            retry_after = _extract_retry_after(e)
            if retry_after is not None:
                trace.info("provider_retry_after", seconds=retry_after, attempt=attempt)
                await asyncio.sleep(retry_after)
                continue
            delay = RETRY_BASE_DELAY * (
                RETRY_BACKOFF_FACTOR**attempt
            ) + random.uniform(0, RETRY_JITTER)
            trace.warning("provider_retry", attempt=attempt, delay=delay, error=str(e))
            await asyncio.sleep(delay)
        if attempt == MAX_RETRIES - 1:
            raise ProviderError(str(last_err), status=getattr(last_err, "status_code", None))


# ---------------------------------------------------------------------------
# 消息摘要（上下文压缩用）
# ---------------------------------------------------------------------------


def _heuristic_summary(messages: list[dict], max_chars: int = 1200) -> str:
    """无模型时的启发式摘要：拼接每条消息的角色与开头内容。"""
    parts = []
    for m in messages[:80]:
        role = m.get("role")
        if role == "user":
            tag = "用户"
        elif role == "assistant":
            tag = "助手"
        elif role == "tool":
            tag = "工具"
        else:
            tag = str(role)
        content = str(m.get("content") or "").replace("\n", " ").strip()
        if m.get("tool_calls"):
            content = (content + " [调用工具]").strip()
        if not content:
            continue
        parts.append(f"{tag}：{content[:100]}")
    text = "；".join(parts)
    return text[:max_chars]


async def summarize_messages(cfg: dict, messages: list[dict]) -> str:
    """把一段旧对话压缩成要点摘要（用于上下文满窗时腾空间）。"""
    if cfg.get("model") == "mock" or not cfg.get("api_key"):
        return "（早期对话摘要）" + _heuristic_summary(messages)

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
        )
        content = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        if content:
            return "（早期对话摘要）" + content[:1500]
    except Exception:  # noqa: BLE001  —— 摘要失败不阻断对话，退回启发式
        pass
    return "（早期对话摘要）" + _heuristic_summary(messages)


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
