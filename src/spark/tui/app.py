from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from spark.config import SparkConfig, require_api_key
from spark.core.loop import AgentLoop
from spark.models import ApprovalDecision, ApprovalRequest, TurnEvent
from spark.providers.base import Provider
from spark.providers.probe import probe_provider
from spark.store import SessionStore
from spark.tui.welcome import error_hint, help_text, status_text, welcome_text
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolRegistry


class HelpScreen(ModalScreen[None]):
    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("使用帮助", id="title"),
            RichLog(id="help-body", wrap=True, markup=True),
            Horizontal(Button("关闭 (Esc)", id="close", variant="primary")),
            id="dialog",
        )

    def on_mount(self) -> None:
        self.call_after_refresh(self._fill_body)

    def _fill_body(self) -> None:
        try:
            self.query_one("#help-body", RichLog).write(help_text())
        except NoMatches:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key in {"escape", "question_mark"}:
            self.dismiss(None)


class ApprovalScreen(ModalScreen[ApprovalDecision]):
    def __init__(
        self, request: ApprovalRequest, access_mode: str = "workspace"
    ) -> None:
        super().__init__()
        self.request = request
        self.access_mode = access_mode

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(
                f"是否允许执行 {self.request.tool_call.name}？  [访问级别={self.access_mode}]",
                id="title",
            ),
            RichLog(id="detail", wrap=True),
            Horizontal(
                Button("允许", id="allow", variant="success"),
                Button("拒绝", id="deny", variant="error"),
                Button("始终允许", id="always", variant="primary"),
            ),
            id="dialog",
        )

    def on_mount(self) -> None:
        log = self.query_one("#detail", RichLog)
        log.write(self.request.diff or self.request.summary)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "allow": "allow",
            "deny": "deny",
            "always": "allow_always",
        }
        action = mapping[event.button.id or "deny"]
        self.dismiss(
            ApprovalDecision(tool_call_id=self.request.tool_call.id, action=action)
        )


class SparkApp(App):
    TITLE = "Spark"
    CSS = """
    #status { height: 1; color: cyan; }
    #toolbar { height: 3; }
    #chat { height: 1fr; }
    #composer { dock: bottom; }
    #dialog { padding: 1 2; }
    #help-body { height: 1fr; }
    """
    BINDINGS = [
        Binding("ctrl+c", "cancel_turn", "Cancel turn", show=True),
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_chat", "Clear view", show=True),
        Binding("ctrl+t", "test_model", "Test model", show=True),
        Binding("question_mark", "toggle_help", "Help", show=True),
    ]

    def __init__(
        self,
        *,
        workdir: Path,
        cfg: SparkConfig,
        store: SessionStore,
        session_id: str,
        provider: Provider,
        registry: ToolRegistry,
        bridge: McpBridge | None,
        initial_prompt: str | None = None,
        memory=None,
    ) -> None:
        super().__init__()
        self.workdir = workdir
        self.cfg = cfg
        self.store = store
        self.session_id = session_id
        self.bridge = bridge
        self.initial_prompt = initial_prompt
        self.loop_engine = AgentLoop(
            workdir=workdir,
            cfg=cfg,
            provider=provider,
            registry=registry,
            store=store,
            session_id=session_id,
            approver=self._approve,
            memory=memory,
        )
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._status_text(), id="status")
        yield Horizontal(
            Button("Test model", id="test-model", variant="primary"),
            Button("Help (?)", id="help", variant="default"),
            id="toolbar",
        )
        yield RichLog(id="chat", wrap=True, highlight=True, markup=True)
        yield Input(
            placeholder="Describe a task and press Enter (? for help)", id="composer"
        )
        yield Footer()

    def _status_text(self) -> str:
        return status_text(self.cfg, self.session_id, str(self.workdir))

    def on_mount(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.write(welcome_text(self.cfg, str(self.workdir)))
        restored = 0
        for msg in self.loop_engine.history:
            if msg.role in {"user", "assistant"} and msg.content:
                chat.write(f"{msg.role}: {msg.content}")
                restored += 1
        if restored:
            chat.write(f"（已恢复 {restored} 条历史消息）")
        if self.initial_prompt:
            self.run_worker(self._run_prompt(self.initial_prompt), exclusive=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text or self._running:
            return
        self.run_worker(self._run_prompt(text), exclusive=True)

    def action_cancel_turn(self) -> None:
        self.loop_engine.cancel()

    def action_clear_chat(self) -> None:
        self.query_one("#chat", RichLog).clear()

    def action_test_model(self) -> None:
        if self._running:
            return
        self.run_worker(self._test_model(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test-model":
            self.action_test_model()
        elif event.button.id == "help":
            self.action_toggle_help()

    def action_toggle_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
        else:
            self.push_screen(HelpScreen())

    async def _test_model(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.write("正在测试模型连通性…")
        try:
            key = require_api_key(self.cfg) or ""
            result = await probe_provider(
                base_url=self.cfg.provider.base_url,
                api_key=key,
                model=self.cfg.provider.model,
                rounds=2,
            )
        except Exception as exc:
            chat.write(f"[red]测试失败[/red] {exc}")
            chat.write(f"[yellow]{error_hint(str(exc))}[/yellow]")
            return
        if result.ok:
            chat.write(
                f"[green]连通正常[/green] {result.latency_ms}ms x{result.rounds}"
            )
            chat.write(f"content: {result.content}")
            chat.write(f"stream: {result.stream_content}")
        else:
            chat.write(f"[red]测试失败[/red] {result.error}")
            chat.write(f"[yellow]{error_hint(result.error or '')}[/yellow]")

    async def _approve(self, request: ApprovalRequest) -> ApprovalDecision:
        return await self.push_screen_wait(
            ApprovalScreen(request, access_mode=self.cfg.agent.sandbox_mode)
        )

    async def _run_prompt(self, text: str) -> None:
        self._running = True
        chat = self.query_one("#chat", RichLog)
        chat.write(f"[bold cyan]你[/bold cyan] {text}")
        try:
            async for event in self.loop_engine.iter_turn(text):
                self._render(event, chat)
        finally:
            self._running = False

    def _render(self, event: TurnEvent, chat: RichLog) -> None:
        if event.type == "text_delta" and event.text:
            chat.write(event.text)
        elif event.type == "reasoning_delta" and event.text:
            chat.write(f"[thinking] {event.text}")
        elif event.type == "tool_start" and event.tool_call:
            chat.write(f"tool {event.tool_call.name} ...")
        elif event.type == "tool_end" and event.tool_call and event.result:
            payload = str(event.result.payload)
            if len(payload) > 500:
                payload = payload[:500] + "..."
            chat.write(f"tool {event.tool_call.name}: {payload}")
        elif event.type == "plan" and event.data and event.data.get("steps"):
            steps = event.data["steps"]
            chat.write("plan: " + " | ".join(str(s.get("title", "")) for s in steps))
        elif event.type == "context" and event.data and event.data.get("usage"):
            usage = event.data["usage"]
            chat.write(f"context: {usage.get('used')}/{usage.get('limit')} tokens")
        elif (
            event.type == "compaction"
            and event.data
            and event.data.get("before_tokens")
        ):
            chat.write(
                f"compacted: {event.data['before_tokens']} -> {event.data.get('after_tokens')} tokens"
            )
        elif event.type == "turn_error":
            chat.write(f"[red]错误[/red] {event.text}")
            chat.write(f"[yellow]{error_hint(event.text or '')}[/yellow]")
        elif event.type == "turn_end" and event.text:
            pass

    async def on_unmount(self) -> None:
        try:
            self.loop_engine.cancel()
        except Exception:
            pass
        if self.bridge:
            await self.bridge.close()
        self.store.close()
