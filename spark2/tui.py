"""TUI：Textual 终端界面，与 Web 共用 AgentLoop 内核。

设计要点（符合人的使用逻辑）：
- 消息流：用户消息 / 助手消息 / 计划卡 / 可展开工具卡（点标题展开输出）。
- 审批：工具需确认时底部出现审批条，按 A 允许 / D 拒绝 / S 本次始终允许 / Esc 拒绝。
- 快捷键：Enter 发送；Ctrl+N 新建会话；Ctrl+S 会话列表；Ctrl+C 取消当前回合；Ctrl+Q 退出。
- 会话历史从 JSONL 恢复，记忆 / 检查点 / MCP 全部与 Web 同源。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, ScrollableContainer, Vertical
from textual.events import Click
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Static

from spark2.approval import ApprovalGate
from spark2.config import load_config
from spark2.loop import AgentLoop
from spark2.memory import MemoryStore
from spark2.store import SessionStore
from spark2.tools.mcp import McpManager, servers_from_cfg

CSS = """
Screen { background:#0b1220; }
#messages { padding:0 2; }
.msg { margin:1 0; padding:1 2; border:round #22304a; }
.msg.user { background:#122a33; border-left:solid #2dd4bf; }
.msg.assistant { background:#0e1726; }
.think { color:#8fa3bd; }
.plan { color:#2dd4bf; margin:1 0; padding:1 2; border-left:solid #2dd4bf; }
.tool { margin:1 0; padding:0; border:round #22304a; }
.tool .thead { padding:0 1; color:#8fa3bd; }
.tool .thead.ok { color:#4ade80; }
.tool .thead.bad { color:#f87171; }
#approval { display:none; margin:1 0; padding:1 2; background:#2a1f12; border:round #f59e0b; }
#approval.visible { display:block; }
#inputbar { padding:1 2; border-top:solid #22304a; }
#input { background:#0e1726; border:solid #22304a; }
#sessionLine { padding:0 2; color:#8fa3bd; }
SessionListScreen { align:center middle; }
SessionListScreen #sltitle { padding:1 2; color:#2dd4bf; }
SessionListScreen Button { margin:1 2; width:80%; }
"""


# ---------- 事件（Worker → App） ----------
class EvText(Message):
    def __init__(self, delta: str) -> None:
        super().__init__()
        self.delta = delta


class EvReasoning(Message):
    def __init__(self, delta: str) -> None:
        super().__init__()
        self.delta = delta


class EvPlan(Message):
    def __init__(self, steps: list[str]) -> None:
        super().__init__()
        self.steps = steps


class EvToolStart(Message):
    def __init__(self, name: str, summary: str) -> None:
        super().__init__()
        self.name, self.summary = name, summary


class EvToolResult(Message):
    def __init__(self, name: str, output: str, approved: bool) -> None:
        super().__init__()
        self.name, self.output, self.approved = name, output, approved


class EvApproval(Message):
    def __init__(self, request_id: str, tool: str, summary: str, diff: str) -> None:
        super().__init__()
        self.request_id, self.tool, self.summary, self.diff = request_id, tool, summary, diff


class EvDone(Message):
    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason


class EvError(Message):
    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message


class SessionListScreen(ModalScreen[None]):
    """会话列表（Ctrl+S）。"""

    BINDINGS = [Binding("escape", "dismiss", "关闭")]

    def __init__(self, rows: list[dict], current: str | None) -> None:
        super().__init__()
        self.rows, self.current = rows, current

    def compose(self) -> ComposeResult:
        yield Static("会话列表（Esc 关闭）", id="sltitle")
        for r in self.rows:
            mark = "● " if r["id"] == self.current else "  "
            yield Button(f"{mark}{r.get('title','?')} · {r.get('workdir','')}", id=f"sl_{r['id']}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id and event.button.id.startswith("sl_"):
            self.dismiss(event.button.id.replace("sl_", ""))


class ApprovalBar(Static):
    pass


class SparkTui(App[None]):
    """Spark 终端界面。"""

    TITLE = "Spark 编程助手"
    CSS = CSS
    BINDINGS = [
        Binding("ctrl+n", "new_session", "新建会话"),
        Binding("ctrl+s", "sessions", "会话列表"),
        Binding("ctrl+c", "cancel", "取消当前回合"),
        Binding("ctrl+q", "quit", "退出"),
        Binding("escape", "cancel", "取消/拒绝"),
        Binding("a", "approve", "允许（审批时）", show=False),
        Binding("d", "deny", "拒绝（审批时）", show=False),
        Binding("s", "always", "始终允许（审批时）", show=False),
    ]

    def __init__(
        self,
        cfg: dict | None = None,
        store: SessionStore | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        super().__init__()
        self.cfg = cfg or load_config()
        self.store = store or SessionStore()
        self.memory = memory or MemoryStore()
        self.sid: str | None = None
        self._agent: AgentLoop | None = None
        self._assistant_text = ""
        self._tool_widgets: dict[str, Container] = {}
        self._pending: dict | None = None

    # ---------- 组装 ----------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("", id="sessionLine")
        yield ScrollableContainer(id="messages")
        yield ApprovalBar("", id="approval")
        yield Vertical(Input(placeholder="描述你想做的事，回车发送；Ctrl+N 新建会话", id="input"), id="inputbar")
        yield Footer()

    def on_mount(self) -> None:
        rows = self.store.list(limit=1)
        if rows:
            self.sid = rows[0]["id"]
        else:
            self._create_session()
        self._refresh_line()
        self._render_history()
        self.query_one("#input", Input).focus()

    # ---------- 会话 ----------
    def _create_session(self) -> None:
        meta = self.store.create(str(self.cfg.get("workdir") or Path.cwd()), str(self.cfg.get("model") or ""))
        self.sid = meta["id"]

    def _refresh_line(self) -> None:
        self.query_one("#sessionLine", Static).update(
            f"会话 {self.sid} · {self.cfg.get('workdir','')} · 模型 {self.cfg.get('model','')}"
        )

    def _render_history(self) -> None:
        msgs = self.store.messages(self.sid) if self.sid else []
        box = self.query_one("#messages", ScrollableContainer)
        box.remove_children()
        self._tool_widgets.clear()
        self._assistant_text = ""
        for m in msgs:
            if m.get("role") == "user":
                box.mount(Static(str(m.get("content", "")), classes="msg user"))
            elif m.get("role") == "assistant":
                box.mount(Static(str(m.get("content", "")), classes="msg assistant"))

    def action_new_session(self) -> None:
        if self._agent is not None:
            self.notify("当前回合未结束，先按 Ctrl+C 取消", severity="warning")
            return
        self._create_session()
        self._refresh_line()
        self._render_history()
        self.notify("已新建会话")

    def action_sessions(self) -> None:
        rows = self.store.list(limit=20)
        if not rows:
            self.notify("还没有其他会话", severity="warning")
            return

        def done(sid: str | None) -> None:
            if sid and sid != self.sid:
                self.sid = sid
                self._refresh_line()
                self._render_history()

        self.push_screen(SessionListScreen(rows, self.sid), done)

    # ---------- 发送 ----------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        event.input.value = ""
        if not prompt or self._agent is not None:
            return
        box = self.query_one("#messages", ScrollableContainer)
        box.mount(Static(prompt, classes="msg user"))
        box.scroll_end(animate=False)
        self.run_worker(self.run_agent(prompt), name="agent")

    async def run_agent(self, prompt: str) -> None:
        sid = self.sid
        if not sid:
            return
        cfg = self.cfg
        gate = ApprovalGate(mode=str(cfg.get("approval_mode") or "suggest"))
        provider_cfg = {
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "api_key": cfg.get("api_key", ""),
        }
        if cfg.get("mock_script"):
            provider_cfg["mock_script"] = cfg["mock_script"]
        mcp: McpManager | None = None
        if cfg.get("mcp_servers"):
            mcp = McpManager(servers_from_cfg(cfg))
        self._agent = AgentLoop(
            Path(cfg.get("workdir") or Path.cwd()),
            provider_cfg,
            gate,
            max_context_tokens=int(cfg.get("max_context_tokens", 32000)),
            memory=self.memory,
            mcp=mcp,
        )
        messages = self.store.messages(sid)
        self.store.append(sid, {"role": "user", "content": prompt})
        self._assistant_text = ""
        self.query_one("#input", Input).disabled = True
        try:
            async for ev in self._agent.stream(messages):
                t = ev.get("type")
                if t == "text":
                    self._assistant_text += ev.get("delta", "")
                    self.post_message(EvText(ev.get("delta", "")))
                elif t == "reasoning":
                    self.post_message(EvReasoning(ev.get("delta", "")))
                elif t == "plan":
                    self.post_message(EvPlan(ev.get("steps", [])))
                elif t == "tool_start":
                    self.post_message(EvToolStart(ev.get("name", ""), ev.get("args_summary", "")))
                elif t == "tool_result":
                    self.post_message(EvToolResult(ev.get("name", ""), ev.get("output", ""), ev.get("approved", False)))
                elif t == "approval":
                    self.post_message(
                        EvApproval(ev["request_id"], ev.get("tool", ""), ev.get("summary", ""), ev.get("diff", ""))
                    )
                elif t == "error":
                    self.post_message(EvError(ev.get("message", "")))
                elif t == "done":
                    self.post_message(EvDone(ev.get("reason", "")))
                    break
            if self._assistant_text:
                self.store.append(sid, {"role": "assistant", "content": self._assistant_text})
        except asyncio.CancelledError:
            if self._agent is not None:
                await self._agent.cancel()
            raise
        finally:
            if mcp is not None:
                mcp.close()
            self._agent = None
            self._pending = None
            bar = self.query_one("#approval", ApprovalBar)
            bar.remove_class("visible")
            bar.update("")
            inp = self.query_one("#input", Input)
            inp.disabled = False
            inp.focus()

    # ---------- 渲染 ----------
    def on_ev_text(self, ev: EvText) -> None:
        box = self.query_one("#messages", ScrollableContainer)
        last = None
        for c in box.children:
            if isinstance(c, Static) and "assistant" in c.classes:
                last = c
        if last is None:
            last = Static("", classes="msg assistant")
            box.mount(last)
        last.update(self._assistant_text)
        box.scroll_end(animate=False)

    def on_ev_reasoning(self, ev: EvReasoning) -> None:
        box = self.query_one("#messages", ScrollableContainer)
        box.mount(Static(ev.delta, classes="think"))
        box.scroll_end(animate=False)

    def on_ev_plan(self, ev: EvPlan) -> None:
        box = self.query_one("#messages", ScrollableContainer)
        box.mount(Static("计划： " + " → ".join(ev.steps), classes="plan"))
        box.scroll_end(animate=False)

    async def on_ev_tool_start(self, ev: EvToolStart) -> None:
        box = self.query_one("#messages", ScrollableContainer)
        card = Container(Static(f"▶ {ev.name}  {ev.summary}", classes="thead"), classes="tool")
        await box.mount(card)
        self._tool_widgets[ev.name] = card
        box.scroll_end(animate=False)

    async def on_ev_tool_result(self, ev: EvToolResult) -> None:
        card = self._tool_widgets.get(ev.name)
        if card is None:
            box = self.query_one("#messages", ScrollableContainer)
            card = Container(Static("", classes="thead"), classes="tool")
            await box.mount(card)
        head = card.query_one(".thead", Static)
        head.remove_class("ok")
        head.remove_class("bad")
        head.add_class("ok" if ev.approved else "bad")
        mark = "✓" if ev.approved else "✗"
        label = f"{mark} {ev.name}" + ("（已批准）" if ev.approved else "（已拒绝）")
        if ev.output:
            label += "\n" + ev.output[:4000]
        head.update(label)
        box = self.query_one("#messages", ScrollableContainer)
        box.scroll_end(animate=False)

    def on_ev_approval(self, ev: EvApproval) -> None:
        self._pending = {"request_id": ev.request_id, "tool": ev.tool}
        bar = self.query_one("#approval", ApprovalBar)
        diff = (ev.diff or "")[:600]
        bar.update(f"等待确认：{ev.tool} — {ev.summary}\n{diff}\n[A] 允许  [D] 拒绝  [S] 本次始终允许  [Esc] 拒绝")
        bar.add_class("visible")
        box = self.query_one("#messages", ScrollableContainer)
        box.scroll_end(animate=False)

    def on_ev_done(self, ev: EvDone) -> None:
        label = {"done": "完成", "cancelled": "已取消", "max_turns": "达到最大轮次", "error": "出错"}.get(ev.reason, ev.reason)
        self.notify(label)

    def on_ev_error(self, ev: EvError) -> None:
        box = self.query_one("#messages", ScrollableContainer)
        box.mount(Static("错误： " + ev.message, classes="msg assistant"))
        box.scroll_end(animate=False)

    # ---------- 审批按键 ----------
    def _answer(self, action: str) -> None:
        if self._pending is None or self._agent is None:
            return
        self._agent.gate.respond(self._pending["request_id"], action, self._pending["tool"])
        self._pending = None
        bar = self.query_one("#approval", ApprovalBar)
        bar.remove_class("visible")
        bar.update("")

    def action_approve(self) -> None:
        self._answer("allow")

    def action_deny(self) -> None:
        self._answer("deny")

    def action_always(self) -> None:
        self._answer("always")

    def action_cancel(self) -> None:
        if self._pending is not None:
            self._answer("deny")
            return
        loop = self._agent
        if loop is not None:
            self.run_worker(self._do_cancel(loop), name="cancel")

    async def _do_cancel(self, loop: AgentLoop) -> None:
        await loop.cancel()

    # ---------- 工具卡点击展开 ----------
    def on_click(self, event: Click) -> None:
        if isinstance(event.widget, Container) and "tool" in event.widget.classes:
            event.widget.toggle_class("open")


def main(cfg: dict | None = None) -> None:
    SparkTui(cfg=cfg).run()


if __name__ == "__main__":
    main()
