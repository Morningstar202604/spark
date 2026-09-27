"""Provider 层：OpenAI 兼容流式客户端 + 演示 mock + token 估算。

- 原生支持流式 content / reasoning_content（DeepSeek R1 风格）/ tool_calls。
- 不引入任何额外依赖：HTTP 用 httpx，估算用启发式（安装 tiktoken 后自动用更准的近似）。
- mock 提供脚本化事件流，用于测试与无密钥演示。
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import httpx

# tiktoken 惰性初始化：get_encoding 首次调用需联网下载 BPE 词表，
# 网络受限环境下既不能阻断 import，也不能阻断应用启动——失败一律回退启发式。
_ENCODER: object | None = None
_ENCODER_TRIED = False


def _get_encoder():
    global _ENCODER, _ENCODER_TRIED
    if not _ENCODER_TRIED:
        _ENCODER_TRIED = True
        try:
            import tiktoken  # type: ignore

            _ENCODER = tiktoken.get_encoding("o200k_base")
        except Exception:  # noqa: BLE001 —— 未安装 / 下载失败 / 缓存损坏都回退
            _ENCODER = None
    return _ENCODER


def estimate_tokens(text: str) -> int:
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    return _heuristic_tokens(text)


def _heuristic_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E80)
    other = len(text) - cjk
    return int(cjk * 1.5 + other / 4) + 8


class ProviderError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def _opt_float(v) -> float | None:
    """配置里的可选数值（可能是空串/字符串数字）；无效 → None（不传该参数）。"""
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
    """把一段旧对话压缩成要点摘要（用于上下文满窗时腾空间）。

    - mock / 无密钥：启发式拼接（不产生额外调用，测试与演示同路径）；
    - 真实模型：一次非流式调用生成中文要点摘要（仅在超窗时触发）。
    """
    if cfg.get("model") == "mock" or not cfg.get("api_key"):
        return "（早期对话摘要）" + _heuristic_summary(messages)

    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        return "（早期对话摘要）" + _heuristic_summary(messages)
    url = base + "/chat/completions"
    compact_text = _heuristic_summary(messages, max_chars=6000)
    payload = {
        "model": cfg.get("model") or "deepseek-v4-pro",
        "messages": [
            {
                "role": "system",
                "content": "把下面的早期对话压缩成 200 字以内的中文要点摘要，保留：关键决定、涉及的文件路径、执行的命令、重要结论与未完成事项。只输出摘要本身。",
            },
            {"role": "user", "content": compact_text},
        ],
        "stream": False,
        "max_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {cfg.get('api_key', '')}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=20)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                return "（早期对话摘要）" + _heuristic_summary(messages)
            data = resp.json()
            content = (
                (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            )
            if content and content.strip():
                return "（早期对话摘要）" + content.strip()[:1500]
            return "（早期对话摘要）" + _heuristic_summary(messages)
    except Exception:  # noqa: BLE001 —— 摘要失败不阻断对话，退回启发式
        return "（早期对话摘要）" + _heuristic_summary(messages)


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

    base = (cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise ProviderError("未配置模型接口地址")
    url = base + "/chat/completions"
    payload: dict = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": messages,
        "stream": True,
    }
    # 可选采样参数：仅当设置里显式配置才发送（空 = 用模型服务端默认）
    temp = _opt_float(cfg.get("temperature"))
    if temp is not None:
        payload["temperature"] = temp
    mt = _opt_int(cfg.get("max_tokens"))
    if mt is not None:
        payload["max_tokens"] = mt
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {cfg.get('api_key', '')}"}

    input_est = sum(estimate_tokens(str(m)) for m in messages)
    if tools:
        input_est += sum(estimate_tokens(str(t)) for t in tools)

    async def _once() -> AsyncIterator[dict]:
        """单次请求：产出文本/推理/工具调用/用量事件，失败抛 ProviderError。"""
        acc: dict[int, dict] = {}
        out_tokens = 0
        finish_reason: str | None = None
        real_prompt = real_completion = None
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as client:
            async with client.stream(
                "POST", url, json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:400]
                    raise ProviderError(
                        f"模型接口返回 {resp.status_code}: {body}",
                        status=resp.status_code,
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "error" in chunk:
                        raise ProviderError(f"模型返回错误: {chunk['error']}")
                    # 网关结尾若带回真实用量则优先采用（否则退回本地估算）
                    u = chunk.get("usage")
                    if isinstance(u, dict):
                        real_prompt = int(u.get("prompt_tokens") or 0)
                        real_completion = int(u.get("completion_tokens") or 0)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    ch = choices[0]
                    if ch.get("finish_reason"):
                        finish_reason = ch["finish_reason"]
                    delta = ch.get("delta") or {}
                    if delta.get("content"):
                        text = delta["content"]
                        out_tokens += estimate_tokens(text)
                        yield {"type": "text", "text": text}
                        if max_delta is not None and out_tokens > max_delta:
                            return
                    if delta.get("reasoning_content"):
                        yield {"type": "reasoning", "text": delta["reasoning_content"]}
                    tcs = delta.get("tool_calls")
                    if tcs:
                        for tc in tcs:
                            idx = tc.get("index", 0)
                            slot = acc.setdefault(
                                idx, {"id": "", "name": "", "arguments": ""}
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
        calls = [v for _, v in sorted(acc.items())]
        for c in calls:
            raw = c["arguments"]
            try:
                c["arguments"] = json.loads(raw or "{}")
            except json.JSONDecodeError:
                # 输出被长度上限截断：arguments 不是完整 JSON。绝不能当成 {} 静默执行
                # （会写出空文件/错误参数），标记出来交由上层回问模型重试。
                c["arguments"] = {}
                c["invalid_arguments"] = True
                c["arguments_tail"] = raw[-200:]
        if calls:
            yield {"type": "tool_calls", "calls": calls}
        yield {
            "type": "usage",
            "estimated": input_est + out_tokens,
            "prompt_tokens": real_prompt if real_prompt is not None else input_est,
            "completion_tokens": (
                real_completion if real_completion is not None else out_tokens
            ),
            "model": cfg.get("model") or "unknown",
            "finish_reason": finish_reason,
        }

    # 瞬态故障（429/5xx/上游错误）且尚未产出任何内容时重试，最多 3 次；
    # 4xx 配置类错误与已产出部分内容后断流不重试（避免重复执行 / 掩盖配置问题）。
    last_err: Exception | None = None
    for attempt in range(3):
        produced = False
        try:
            async for ev in _once():
                produced = True
                yield ev
            return
        except ProviderError as e:
            retryable = e.status is None or e.status in _RETRYABLE_STATUS
            if produced or not retryable:
                raise
            last_err = e
        except httpx.HTTPError as e:
            if produced:
                raise
            last_err = ProviderError(f"请求模型失败: {e}")
        if attempt == 2:
            raise last_err
        await asyncio.sleep(1 + attempt * 2)


async def _mock_chat(
    cfg: dict, messages: list[dict], tools: list[dict] | None
) -> AsyncIterator[dict]:
    """演示/测试用的脚本化 Provider。cfg["mock_script"] 为轮次列表：
    [ [事件...], [事件...] ]，每轮一组事件；耗尽后回复固定文本。
    """
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
    # 默认演示行为：回应一句，并尝试列出当前目录（展示工具卡与审批）。
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            user_text = m["content"]
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
