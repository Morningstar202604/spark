"""Agent 循环：单 asyncio 事件流驱动，行为符合"人的逻辑"。

事件协议（全部可 JSON 序列化，前端直接消费）：
  {"type":"status","text":str}
  {"type":"plan","steps":list[str]}
  {"type":"reasoning","delta":str}
  {"type":"text","delta":str}
  {"type":"usage","estimated":int}
  {"type":"tool_start","id","name","args_summary","diff"}
  {"type":"approval","request_id","tool","summary","diff","reason"}
  {"type":"tool_result","id","name","output","approved","duration_ms"}
  {"type":"error","message"}
  {"type":"done","reason":"done|max_turns|cancelled|error"}

本文件只保留"编排"：初始化、系统提示、记忆注入、模型请求、截断重试、事件产出。
已按职责拆出的兄弟模块：
  routing.py     —— 快/强模型路由（route_model）
  truncation.py  —— 输出截断判定（_looks_truncated / _TRUNCATION_NUDGE）
  compaction.py  —— 上下文压缩 + 孤儿消息清理（compact_messages / strip_orphans）
  prompt.py      —— 系统提示词与记忆模板常量
  execution.py   —— 工具执行层（ToolExecutor：审批→检查点→执行→注入→回填）

为兼容既有 import（web/tui/cli/subagent 与测试均 from spark.loop import ...），
上述模块的符号在 import 区再导出，行为与拆分前完全一致。

设计要点：
- 与旧版相反：不在线程里新建事件循环，整个 loop 就是 async 生成器，由调用方（FastAPI/CLI）驱动。
- 计划先行：模型通过 update_plan 展示计划；写文件前由审批门出统一 diff。
- 取消：置 cancel_event + 强杀进程组 + 使未决审批全部失效。
- 无任何按轮计费的隐性 LLM 调用（无自动标题/记忆抽取）。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from spark.approval import ApprovalGate
from spark.compaction import compact_messages, strip_orphans
from spark.config import config_dir
from spark.execution import ToolExecutor
from spark.memory import MemoryStore, make_embedder
from spark.prompt import MEMORY_CONTEXT_TEMPLATE, SYSTEM_PROMPT_TEMPLATE
from spark.provider import ProviderError, stream_chat

# 兄弟模块（各自只做一件事，见模块 docstring）——同时作为兼容性再导出，
# 拆分前这些符号就能从 spark.loop 导入，测试与调用方（web/tui/cli/subagent）无感。
from spark.routing import route_model
from spark.tools import build_registry, tool_schemas
from spark.tools.base import Tool, ToolContext
from spark.tools.mcp import McpManager
from spark.truncation import _TRUNCATION_NUDGE, _looks_truncated

# 兼容性再导出：拆分前测试与调用方从 spark.loop 导入该符号
_strip_orphans = strip_orphans

DEFAULT_TIMEOUT_S = 180.0


class AgentLoop:
    def __init__(
        self,
        workdir: Path,
        provider_cfg: dict,
        gate: ApprovalGate | None = None,
        registry: dict[str, Tool] | None = None,
        max_turns: int = 25,
        max_context_tokens: int = 32000,
        memory: MemoryStore | None = None,
        mcp: McpManager | None = None,
        log_path: Path | None = None,
        cancel_event: asyncio.Event | None = None,
        system_prompt_text: str | None = None,
        tool_timeout: float = DEFAULT_TIMEOUT_S,
        extra_protected: list[str] | None = None,
        auto_verify: bool = True,
    ) -> None:
        self.workdir = workdir.resolve()
        self.provider_cfg = dict(provider_cfg)
        self.gate = gate or ApprovalGate(mode="suggest")
        self.registry = registry or build_registry()
        self.max_turns = max_turns
        self.max_context_tokens = max_context_tokens
        self.tool_timeout = float(tool_timeout)
        self._extra_protected = list(extra_protected or [])
        self.cancel_event = cancel_event or asyncio.Event()
        self.auto_verify = auto_verify
        self.system_prompt_text = system_prompt_text
        self.memory = memory or MemoryStore(embedder=make_embedder(self.provider_cfg))
        self.mcp = mcp
        self.log_path = log_path
        self.ctx = ToolContext(
            workdir=self.workdir,
            protected=self._protected_paths(),
            cancel_event=self.cancel_event,
            memory=self.memory,
            index={},  # 代码索引状态：{workdir: {index, loaded_at}}（惰性构建）
        )
        # 工具执行层：审批 → 检查点 → 执行 → 注入防护 → 回填
        self.executor = ToolExecutor(
            gate=self.gate,
            registry=self.registry,
            ctx=self.ctx,
            cancel_event=self.cancel_event,
            tool_timeout=self.tool_timeout,
            workdir=self.workdir,
            memory=self.memory,
            log_path=self.log_path,
            provider_cfg=self.provider_cfg,
            extra_protected=self._extra_protected,
            auto_verify=self.auto_verify,
        )

    def _protected_paths(self) -> list[Path]:
        paths = [
            config_dir().resolve(),  # 动态：遵守 SPARK_HOME
            (self.workdir / ".git").resolve(),
        ]
        # 用户在设置里追加的保护路径（永远拒绝写入）
        for raw in self._extra_protected:
            try:
                p = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if p not in paths:
                paths.append(p)
        return paths

    async def _memory_block(self, messages: list[dict]) -> str | None:
        """对最后一条用户消息做本地检索，拼出可注入上下文块（无则不注入）。

        检索放入线程池：启用语义记忆时，embed 是同步 HTTP/本地模型调用，
        直接执行会阻塞整个单事件循环（所有会话一起暂停）。
        """
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                user_text = m["content"]
                break
        # 多模态消息（content 为 list）只取文本部分做记忆检索
        if isinstance(user_text, list):
            user_text = " ".join(
                str(p.get("text") or "")
                for p in user_text
                if isinstance(p, dict) and p.get("type") == "text" and p.get("text")
            ).strip()
        if not user_text:
            return None
        try:
            hits = await asyncio.to_thread(
                self.memory.search, str(self.workdir), user_text, limit=5
            )
        except Exception:  # noqa: BLE001 —— 记忆库异常不阻断对话
            return None
        if not hits:
            return None
        items = "\n".join(f"- {h['key']}：{h['value']}" for h in hits)
        return MEMORY_CONTEXT_TEMPLATE.format(items=items)

    def system_prompt(self) -> str:
        prot = "、".join(str(p) for p in self.ctx.protected) or "（无）"
        text = self.system_prompt_text or SYSTEM_PROMPT_TEMPLATE
        try:
            base = text.format(workdir=self.workdir, protected=prot)
        except (KeyError, IndexError, ValueError):
            # 自定义提示词里含裸 { }（如 JSON 示例）时不崩溃，按原文使用
            base = text
        # 项目级指令文件（对标 CLAUDE.md / .cursorrules / .sparkrules）：
        # 工作目录存在时自动追加为项目规范，优先于全局系统提示词。
        # 保持轻量：只读文件、大小受限（64KB），文件不存在零开销。
        for rule_name in ("CLAUDE.md", ".cursorrules", ".sparkrules", "AGENTS.md", "spark.md"):
            rule_file = self.workdir / rule_name
            try:
                if rule_file.is_file() and rule_file.stat().st_size <= 65536:
                    content = rule_file.read_text(
                        encoding="utf-8", errors="replace"
                    ).strip()
                    if content:
                        base = (
                            base
                            + f"\n\n===== 项目指令（来自 {rule_name}，最高优先级）=====\n"
                            + content
                        )
            except OSError:
                continue
        return base

    async def cancel(self) -> None:
        """取消：置 cancel_event + 进程组级强杀运行中的工具进程 + 使未决审批全部失效。

        修复：此前只对主进程 proc.kill()，shell 派生的子进程可能残留；
        现在复用 shell._kill_group 按进程组 SIGKILL（Windows 用 taskkill /T）。
        """
        self.cancel_event.set()
        from spark.tools.shell import _kill_group

        for proc in list(self.ctx.processes.values()):
            try:
                _kill_group(proc)
            except (ProcessLookupError, OSError):
                pass
        for fut in list(self.gate.pending.values()):
            if not fut.done():
                fut.set_result(False)

    def _log(self, ev: dict) -> None:
        """极简事件日志（JSONL，一行一事件），供复盘与排障；无日志路径时零开销。"""
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), **ev}
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass

    async def stream(self, messages: list[dict]):
        """按轮次驱动模型，产出事件流（每事件同时写入日志）。messages 会被就地追加。"""
        async for ev in self._stream_inner(messages):
            self._log(ev)
            yield ev

    async def _stream_inner(self, messages: list[dict]):
        """内部事件循环（不含日志包装）。"""
        # 懒启动 MCP：把配置的 MCP 服务器工具并入注册表（仅一次）
        if self.mcp is not None:
            await self.mcp.start()
            for t in self.mcp.tools():
                self.registry.setdefault(t.name, t)

        turns = 0
        tools_done = 0  # 本次对话已执行的工具数（跨轮累计）：动过手后句尾不完整视为截断
        while True:
            if self.cancel_event.is_set():
                yield {"type": "done", "reason": "cancelled"}
                return
            turns += 1
            if turns > self.max_turns:
                yield {"type": "done", "reason": "max_turns"}
                return

            msgs = [{"role": "system", "content": self.system_prompt()}]
            mem_block = await self._memory_block(messages)
            if mem_block:
                msgs.append({"role": "system", "content": mem_block})
            msgs += await compact_messages(
                messages, self.provider_cfg, self.max_context_tokens
            )

            # 多模型路由：根据最后一条用户消息的复杂度选择快/强模型（首轮生效，可跨轮）
            provider_cfg = self.provider_cfg
            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user" and m.get("content"):
                    last_user = str(m["content"])
                    break
            fast_model = route_model(self.provider_cfg, last_user)
            if fast_model:
                provider_cfg = dict(self.provider_cfg)
                provider_cfg["model"] = fast_model
                yield {
                    "type": "status",
                    "text": f"简单任务 → 自动使用快速模型 {fast_model}",
                }

            sink: dict = {}
            try:
                async for ev in self._stream_model(msgs, provider_cfg, sink):
                    yield ev
            except ProviderError as e:
                yield {"type": "error", "message": str(e)}
                yield {"type": "done", "reason": "error"}
                return

            if self.cancel_event.is_set():
                yield {"type": "done", "reason": "cancelled"}
                return

            assistant_text = sink.get("text") or ""
            calls: list[dict] = sink.get("calls") or []
            saw_tool = bool(calls)

            # 网关明确报 length = 输出被截断，必然要重试；报 stop 但句尾不完整且本轮
            # 已经动过手（工具已执行）时，也按"半途停下"处理。纯问答轮不重试，
            # 避免把"回答里没写句号"误判成截断而多余地催模型动手。
            truncated = _looks_truncated(
                assistant_text, sink.get("finish_reason"), tools_done > 0
            )
            if not saw_tool and truncated:
                # 输出在长度上限处被截断（网关有时报 stop 而非 length），模型没真正动手：
                # 带一条临时系统提示重试一次，要求分批执行。不写入历史，避免污染上下文。
                yield {
                    "type": "status",
                    "text": "模型输出被长度上限截断，已要求它分批继续。",
                }
                retry_sink: dict = {}
                retry_msgs = msgs + [
                    {"role": "system", "content": _TRUNCATION_NUDGE}
                ]
                try:
                    async for ev in self._stream_model(
                        retry_msgs, provider_cfg, retry_sink
                    ):
                        yield ev
                except ProviderError as e:
                    yield {"type": "error", "message": str(e)}
                    yield {"type": "done", "reason": "error"}
                    return
                assistant_text = retry_sink.get("text") or ""
                calls = retry_sink.get("calls") or []
                saw_tool = bool(calls)
                if self.cancel_event.is_set():
                    yield {"type": "done", "reason": "cancelled"}
                    return

            if not saw_tool:
                if assistant_text:
                    messages.append({"role": "assistant", "content": assistant_text})
                yield {"type": "done", "reason": "done"}
                return

            # 记录 assistant 消息（含工具调用），再逐条执行
            asst: dict = {"role": "assistant", "content": assistant_text or None}
            tcs = []
            for c in calls:
                tcs.append(
                    {
                        "id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": c.get("name", ""),
                            "arguments": json.dumps(
                                c.get("arguments") or {}, ensure_ascii=False
                            ),
                        },
                    }
                )
            asst["tool_calls"] = tcs
            messages.append(asst)

            for c in calls:
                if self.cancel_event.is_set():
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": c.get("id", ""),
                            "content": "已取消",
                        }
                    )
                    break
                tools_done += 1
                async for ev in self.executor.execute(messages, c):
                    yield ev

    async def _stream_model(self, msgs: list[dict], provider_cfg: dict, sink: dict):
        """请求模型一轮（带备用模型故障切换）：透传事件到事件流，并把本轮结果写进 sink。

        sink: {"text": str, "calls": list[dict], "finish_reason": str|None}
        主模型不可用（配置/网络/上游错误）时，若配置了 fallback_model 且不是当前模型，
        自动用备用模型重试一轮（对标 Claude Code fallback model / Cursor 备用模型）。
        """
        try:
            async for ev in self._stream_model_once(msgs, provider_cfg, sink):
                yield ev
        except ProviderError as e:
            fb = str(provider_cfg.get("fallback_model") or "").strip()
            cur = provider_cfg.get("model")
            if fb and fb != cur:
                yield {
                    "type": "status",
                    "text": f"主模型不可用（{e}），已自动切换备用模型 {fb}",
                }
                sink.clear()
                fb_cfg = dict(provider_cfg)
                fb_cfg["model"] = fb
                async for ev in self._stream_model_once(msgs, fb_cfg, sink):
                    yield ev
            else:
                raise

    async def _stream_model_once(self, msgs: list[dict], provider_cfg: dict, sink: dict):
        """单次请求模型一轮（无 fallback）：透传事件到事件流，并把本轮结果写进 sink。"""
        sink.setdefault("text", "")
        async for ev in stream_chat(provider_cfg, msgs, tool_schemas(self.registry)):
            if ev["type"] == "text":
                sink["text"] += ev["text"]
                yield {"type": "text", "delta": ev["text"]}
            elif ev["type"] == "reasoning":
                yield {"type": "reasoning", "delta": ev["text"]}
            elif ev["type"] == "tool_calls":
                sink["calls"] = ev["calls"]
            elif ev["type"] == "usage":
                if ev.get("finish_reason"):
                    sink["finish_reason"] = ev["finish_reason"]
                # 透传完整用量（model / 输入输出拆分），供成本面板与日志使用
                yield {
                    "type": "usage",
                    "estimated": ev.get("estimated", 0),
                    "model": ev.get("model"),
                    "prompt_tokens": ev.get("prompt_tokens"),
                    "completion_tokens": ev.get("completion_tokens"),
                    "finish_reason": sink.get("finish_reason"),
                }
