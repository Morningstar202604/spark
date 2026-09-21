from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from spark.config import SparkConfig, require_api_key
from spark.core.loop import AgentLoop
from spark.models import ApprovalDecision, ApprovalRequest, TurnEvent
from spark.providers.base import Provider
from spark.providers.probe import probe_provider
from spark.store import SessionStore
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolRegistry


class ApprovalScreen(ModalScreen[ApprovalDecision]):
    def __init__(self, request: ApprovalRequest, access_mode: str = "workspace") -> None:
        super().__init__()
        self.request = request
        self.access_mode = access_mode

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(f"Approve {self.request.tool_call.name}?  [access={self.access_mode}]", id="title"),
            RichLog(id="detail", wrap=True),
            Horizontal(
                Button("Allow", id="allow", variant="success"),
                Button("Deny", id="deny", variant="error"),
                Button("Allow always", id="always", variant="primary"),
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
        self.dismiss(ApprovalDecision(tool_call_id=self.request.tool_call.id, action=action))


class SparkApp(App):
    CSS = """
    #status { height: 1; color: cyan; }
    #toolbar { height: 3; }
    #chat { height: 1fr; }
    #composer { dock: bottom; }
    #dialog { padding: 1 2; }
    """
    BINDINGS = [
        Binding("ctrl+c", "cancel_turn", "Cancel turn", show=True),
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_chat", "Clear view", show=True),
        Binding("ctrl+t", "test_model", "Test model", show=True),
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
        )
        self._running = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._status_text(), id="status")
        yield Horizontal(Button("Test model", id="test-model", variant="primary"), id="toolbar")
        yield RichLog(id="chat", wrap=True, highlight=True)
        yield Input(placeholder="Describe a task and press Enter", id="composer")
        yield Footer()

    def _status_text(self) -> str:
        return (
            f"model={self.cfg.provider.model}  "
            f"provider={self.cfg.provider.name}  "
            f"base={self.cfg.provider.base_url}  "
            f"approval={self.cfg.agent.approval}  "
            f"access={self.cfg.agent.sandbox_mode}  "
            f"session={self.session_id}"
        )

    def on_mount(self) -> None:
        chat = self.query_one("#chat", RichLog)
        for msg in self.loop_engine.history:
            if msg.role in {"user", "assistant"} and msg.content:
                chat.write(f"{msg.role}: {msg.content}")
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
        self.run_worker(self._test_model(), exclusive=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "test-model":
            self.action_test_model()

    async def _test_model(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.write("testing custom model...")
        try:
            key = require_api_key(self.cfg) or ""
            result = await probe_provider(
                base_url=self.cfg.provider.base_url,
                api_key=key,
                model=self.cfg.provider.model,
                rounds=2,
            )
        except Exception as exc:
            chat.write(f"test FAIL: {exc}")
            return
        if result.ok:
            chat.write(f"test OK ({result.latency_ms}ms x{result.rounds})")
            chat.write(f"content: {result.content}")
            chat.write(f"stream: {result.stream_content}")
        else:
            chat.write(f"test FAIL: {result.error}")

    async def _approve(self, request: ApprovalRequest) -> ApprovalDecision:
        return await self.push_screen_wait(ApprovalScreen(request, access_mode=self.cfg.agent.sandbox_mode))

    async def _run_prompt(self, text: str) -> None:
        self._running = True
        chat = self.query_one("#chat", RichLog)
        chat.write(f"user: {text}")
        try:
            async for event in self.loop_engine.iter_turn(text):
                self._render(event, chat)
        finally:
            self._running = False

    def _render(self, event: TurnEvent, chat: RichLog) -> None:
        if event.type == "text_delta" and event.text:
            chat.write(event.text)
        elif event.type == "tool_start" and event.tool_call:
            chat.write(f"tool {event.tool_call.name} ...")
        elif event.type == "tool_end" and event.tool_call and event.result:
            payload = str(event.result.payload)
            if len(payload) > 500:
                payload = payload[:500] + "..."
            chat.write(f"tool {event.tool_call.name}: {payload}")
        elif event.type == "turn_error":
            chat.write(f"error: {event.text}")
        elif event.type == "turn_end" and event.text:
            pass

    async def on_unmount(self) -> None:
        if self.bridge:
            await self.bridge.close()
        self.store.close()
