"""Agent 循环：基于 LangGraph StateGraph 的编排引擎（内部重构，外部 API 不变）。

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

编排由 StateGraph 驱动：nodes 实现业务逻辑（prepare → call_llm → execute_tool），
conditional edges 实现循环控制（取消/最大轮次/无工具调用 → 终止）。
事件由 nodes 经 LangGraph StreamWriter 产出，外部 API 与事件协议完全不变。

为兼容既有的 import（web/tui/cli/subagent 与测试均 from spark.loop import ...），
兄弟模块的符号在 import 区再导出。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import StreamWriter

from spark.approval import ApprovalGate
from spark.compaction import compact_messages, strip_orphans
from spark.config import config_dir
from spark.execution import ToolExecutor
from spark.memory import MemoryStore, make_embedder
from spark.prompt import MEMORY_CONTEXT_TEMPLATE, SYSTEM_PROMPT_TEMPLATE
from spark.provider import ProviderError, stream_chat

# 兄弟模块（各自只做一件事，见模块 docstring）——同时作为兼容性再导出。
from spark.routing import route_model
from spark.tools import build_registry, tool_schemas
from spark.tools.base import Tool, ToolContext
from spark.tools.mcp import McpManager
from spark.truncation import _TRUNCATION_NUDGE, _looks_truncated

# 兼容性再导出：拆分前测试与调用方从 spark.loop 导入该符号
_strip_orphans = strip_orphans

DEFAULT_TIMEOUT_S = 180.0

# 事件类型白名单——yield 前做类型校验，防拼错 / 静默漏事件
VALID_EVENT_TYPES = frozenset({
    "status", "plan", "reasoning", "text", "usage",
    "tool_start", "approval", "tool_result", "error", "done",
})


def _validate_event(ev: dict) -> dict:
    """yield 前校验事件 type 字段是否合法；trace 级别日志提示，不破环产出。"""
    t = ev.get("type")
    if t not in VALID_EVENT_TYPES:
        import spark.trace as trace
        trace.warning("unknown_event_type", event_type=t, keys=list(ev.keys()))
    return ev


# ──────────────────────────────────────────────────────────────────────────
# LangGraph State 定义
# ──────────────────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    """StateGraph 节点间共享的状态。

    messages 无 reducer：ToolExecutor 对列表原地 append，跨节点可见。
    turns / tools_done 由 prepare / execute_tool 显式返回绝对值（无累加语义）。
    _terminal / _has_calls 驱动 conditional edges 的循环/终止判定。
    _calls / _assistant_text 在 call_llm → execute_tool 间传递本轮工具调用。
    """

    messages: list[dict]           # 对话历史（原地追加）
    turns: int                     # 已执行轮数（非累计：每次返回绝对值）
    tools_done: int                # 跨轮累计执行工具数
    _terminal: bool                # 有节点发出 terminal done 事件时为 True
    _has_calls: bool               # call_llm 产生了工具调用时为 True
    _calls: list[dict]             # 待执行的 tool calls
    _assistant_text: str           # 本轮模型文本输出


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
            index={},
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
        # LangGraph 图在第一次 stream() 时懒编译（避免 __init__ 构建 schema 开销）
        self._graph: object | None = None

    # ── 图编译 ────────────────────────────────────────────────────────────
    def _build_graph(self) -> object:
        """构建 LangGraph StateGraph（仅编译一次）。"""
        if self._graph is not None:
            return self._graph
        graph = StateGraph(AgentState)
        graph.add_node("prepare", self._node_prepare)
        graph.add_node("call_llm", self._node_call_llm)
        graph.add_node("execute_tool", self._node_execute_tool)
        graph.add_edge(START, "prepare")
        graph.add_conditional_edges(
            "prepare",
            self._edge_after_prepare,
            {"call_llm": "call_llm", "end": END},
        )
        graph.add_conditional_edges(
            "call_llm",
            self._edge_after_call_llm,
            {"execute_tool": "execute_tool", "end": END},
        )
        graph.add_edge("execute_tool", "prepare")
        self._graph = graph.compile()
        return self._graph

    def _protected_paths(self) -> list[Path]:
        paths = [
            config_dir().resolve(),
            (self.workdir / ".git").resolve(),
        ]
        for raw in self._extra_protected:
            try:
                p = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if p not in paths:
                paths.append(p)
        return paths

    async def _memory_block(self, messages: list[dict]) -> str | None:
        """对最后一条用户消息做本地检索，拼出可注入上下文块（无则不注入）。"""
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user" and m.get("content"):
                user_text = m["content"]
                break
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
        except (OSError, RuntimeError):
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
            base = text
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
        """取消：置 cancel_event + 进程组级强杀运行中的工具进程 + 使未决审批全部失效。"""
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

    # ── 公共 API ─────────────────────────────────────────────────────────
    async def stream(self, messages: list[dict]):
        """按轮次驱动模型，产出事件流（每事件同时写入日志 + 类型校验）。

        与旧版完全一致的事件协议；内部改用 LangGraph StateGraph 驱动节点编排，
        事件通过 StreamWriter 以 stream_mode="custom" 产出。
        """
        async for ev in self._stream_inner(messages):
            self._log(ev)
            yield _validate_event(ev)

    async def _stream_inner(self, messages: list[dict]):
        """内部事件循环：懒启动 MCP → 构建 LangGraph 图 → astream 驱动。"""
        # 懒启动 MCP：把配置的 MCP 服务器工具并入注册表（仅一次）
        if self.mcp is not None:
            await self.mcp.start()
            for t in self.mcp.tools():
                self.registry.setdefault(t.name, t)

        graph = self._build_graph()
        initial_state: AgentState = {
            "messages": messages,
            "turns": 0,
            "tools_done": 0,
            "_terminal": False,
            "_has_calls": False,
            "_calls": [],
            "_assistant_text": "",
        }
        async for event in graph.astream(
            initial_state, stream_mode="custom"
        ):
            yield event

    # ── LangGraph Nodes ──────────────────────────────────────────────────
    async def _node_prepare(
        self, state: AgentState, *, writer: StreamWriter
    ) -> dict:
        """准备上下文：构建系统提示 + 记忆注入 + 上下文压缩 + 模型路由。

        对应原 _stream_inner while 循环体头部。
        """
        turns = state["turns"] + 1

        # 取消检查（最高优先级）
        if self.cancel_event.is_set():
            writer({"type": "done", "reason": "cancelled"})
            return {"turns": turns, "_terminal": True, "_has_calls": False}

        # 最大轮次检查
        if turns > self.max_turns:
            writer({"type": "done", "reason": "max_turns"})
            return {"turns": turns, "_terminal": True, "_has_calls": False}

        # 构建本轮消息：系统提示 + 记忆块 + 压缩后的历史
        msgs = [{"role": "system", "content": self.system_prompt()}]
        mem_block = await self._memory_block(state["messages"])
        if mem_block:
            msgs.append({"role": "system", "content": mem_block})
        msgs += await compact_messages(
            state["messages"], self.provider_cfg, self.max_context_tokens
        )

        # 多模型路由：根据最后一条用户消息复杂度选择快/强模型
        last_user = ""
        for m in reversed(state["messages"]):
            if m.get("role") == "user" and m.get("content"):
                last_user = str(m["content"])
                break
        fast_model = route_model(self.provider_cfg, last_user)
        if fast_model:
            self._round_provider_cfg = dict(self.provider_cfg, model=fast_model)
            writer({
                "type": "status",
                "text": f"简单任务 → 自动使用快速模型 {fast_model}",
            })
        else:
            self._round_provider_cfg = self.provider_cfg

        self._round_msgs = msgs
        return {"turns": turns, "_terminal": False, "_has_calls": False}

    async def _node_call_llm(
        self, state: AgentState, *, writer: StreamWriter
    ) -> dict:
        """调用模型一轮（含 fallback 切换 + 截断重试）：产 text/reasoning/usage 事件。

        对应原 _stream_model 调用 + 截断检测重试 段。
        """
        msgs: list[dict] = self._round_msgs
        provider_cfg = self._round_provider_cfg
        sink: dict = {}

        # ── 模型调用 + fallback ──
        try:
            async for ev in self._stream_model(msgs, provider_cfg, sink):
                writer(ev)
        except ProviderError as e:
            writer({"type": "error", "message": str(e)})
            writer({"type": "done", "reason": "error"})
            return {"_terminal": True, "_has_calls": False}

        # 模型调用后取消检查
        if self.cancel_event.is_set():
            writer({"type": "done", "reason": "cancelled"})
            return {"_terminal": True, "_has_calls": False}

        assistant_text = sink.get("text") or ""
        calls: list[dict] = sink.get("calls") or []
        saw_tool = bool(calls)

        # ── 截断检测 + 重试（仅纯文本轮 + 动过手的中途）──
        truncated = _looks_truncated(
            assistant_text, sink.get("finish_reason"), state["tools_done"] > 0
        )
        if not saw_tool and truncated:
            writer({
                "type": "status",
                "text": "模型输出被长度上限截断，已要求它分批继续。",
            })
            retry_sink: dict = {}
            retry_msgs = msgs + [
                {"role": "system", "content": _TRUNCATION_NUDGE}
            ]
            try:
                async for ev in self._stream_model(
                    retry_msgs, provider_cfg, retry_sink
                ):
                    writer(ev)
            except ProviderError as e:
                writer({"type": "error", "message": str(e)})
                writer({"type": "done", "reason": "error"})
                return {"_terminal": True, "_has_calls": False}

            assistant_text = retry_sink.get("text") or ""
            calls = retry_sink.get("calls") or []
            saw_tool = bool(calls)

            if self.cancel_event.is_set():
                writer({"type": "done", "reason": "cancelled"})
                return {"_terminal": True, "_has_calls": False}

        # ── 无工具调用 → 本轮结束 ──
        if not saw_tool:
            if assistant_text:
                state["messages"].append(
                    {"role": "assistant", "content": assistant_text}
                )
            writer({"type": "done", "reason": "done"})
            return {"_terminal": True, "_has_calls": False}

        # 有工具调用 → 传递 calls 给 execute_tool
        return {
            "_terminal": False,
            "_has_calls": True,
            "_calls": calls,
            "_assistant_text": assistant_text,
        }

    async def _node_execute_tool(
        self, state: AgentState, *, writer: StreamWriter
    ) -> dict:
        """逐条执行工具调用：产 tool_start/approval/tool_result 事件。

        对应原 executor.execute 循环。
        """
        calls: list[dict] = state["_calls"]
        assistant_text: str = state["_assistant_text"]
        messages: list[dict] = state["messages"]
        tools_done: int = state["tools_done"]

        # 记录 assistant 消息（含工具调用），再逐条执行
        asst: dict = {"role": "assistant", "content": assistant_text or None}
        tcs = []
        for c in calls:
            tcs.append({
                "id": c.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": c.get("name", ""),
                    "arguments": json.dumps(
                        c.get("arguments") or {}, ensure_ascii=False
                    ),
                },
            })
        asst["tool_calls"] = tcs
        messages.append(asst)

        for c in calls:
            if self.cancel_event.is_set():
                messages.append({
                    "role": "tool",
                    "tool_call_id": c.get("id", ""),
                    "content": "已取消",
                })
                break
            tools_done += 1
            async for ev in self.executor.execute(messages, c):
                writer(ev)

        return {"tools_done": tools_done, "_calls": [], "_has_calls": False}

    # ── Conditional Edge Routers ─────────────────────────────────────────
    def _edge_after_prepare(self, state: AgentState) -> str:
        """prepare 后的路由：terminal → END，否则 → call_llm。"""
        if state.get("_terminal"):
            return "end"
        return "call_llm"

    def _edge_after_call_llm(self, state: AgentState) -> str:
        """call_llm 后的路由：terminal 或 无工具调用 → END，否则 → execute_tool。"""
        if state.get("_terminal") or not state.get("_has_calls"):
            return "end"
        return "execute_tool"

    # ── 模型调用层（保持不变）─────────────────────────────────────────────
    async def _stream_model(self, msgs: list[dict], provider_cfg: dict, sink: dict):
        """请求模型一轮（带备用模型故障切换）：透传事件到事件流，并把本轮结果写进 sink。

        sink: {"text": str, "calls": list[dict], "finish_reason": str|None}
        主模型不可用时，若配置了 fallback_model 且不是当前模型，自动用备用模型重试一轮。
        """
        try:
            async for ev in self._stream_model_once(msgs, provider_cfg, sink):
                yield ev
        except ProviderError as e:
            fb = str(provider_cfg.get("fallback_model") or "").strip()
            cur = provider_cfg.get("model")
            if fb and fb != cur:
                writer_ev = {
                    "type": "status",
                    "text": f"主模型不可用（{e}），已自动切换备用模型 {fb}",
                }
                yield writer_ev
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
                yield {
                    "type": "usage",
                    "estimated": ev.get("estimated", 0),
                    "model": ev.get("model"),
                    "prompt_tokens": ev.get("prompt_tokens"),
                    "completion_tokens": ev.get("completion_tokens"),
                    "finish_reason": sink.get("finish_reason"),
                }
