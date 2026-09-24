from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request

from spark.config import SparkConfig
from spark.models import ApprovalRequest, ToolCall, TurnEvent
from spark.store import SessionStore
from spark.web.server import (
    WebApprover,
    _SSESession,
    _cache_control,
    serve_web,
    ui_dir_for,
)


class RecordingWriter:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._lock:
            self.chunks.append(bytes(data))

    def flush(self) -> None:
        return None

    def data(self) -> list[dict]:
        chunks = list(self.chunks)
        payloads: list[dict] = []
        for chunk in chunks:
            for block in chunk.split(b"\n\n"):
                if block.startswith(b"data: "):
                    payloads.append(json.loads(block[6:].decode("utf-8")))
        return payloads


class FailingWriter:
    def __init__(self) -> None:
        self.closed = threading.Event()

    def write(self, data: bytes) -> None:
        raise ConnectionAbortedError("client went away")

    def flush(self) -> None:
        raise ConnectionAbortedError("client went away")


def test_sse_writer_notices_idle_socket_close() -> None:
    server_socket, client_socket = socket.socketpair()
    wfile = server_socket.makefile("wb")
    disconnected = threading.Event()
    session = _SSESession(
        wfile,
        connection=server_socket,
        on_disconnect=disconnected.set,
        close_connection=server_socket.close,
    )
    session.start()
    try:
        client_socket.shutdown(socket.SHUT_RDWR)
        client_socket.close()
        assert disconnected.wait(1.0)
    finally:
        session.abort()
        try:
            wfile.close()
        except OSError:
            pass
        server_socket.close()


def test_sse_writer_merges_text_and_preserves_event_types() -> None:
    writer = RecordingWriter()
    session = _SSESession(
        writer,
        flush_interval=0.02,
        heartbeat_interval=1.0,
        max_text_chars=1024,
    )
    session.start()
    assert session.submit(TurnEvent(type="text_delta", text="a"))
    assert session.submit(TurnEvent(type="text_delta", text="b"))
    assert session.submit(
        TurnEvent(type="tool_start", tool_call=ToolCall(id="t", name="x", arguments={}))
    )
    assert session.finish()

    payloads = writer.data()
    assert payloads[:3] == [
        {"type": "text_delta", "text": "ab"},
        {"type": "tool_start", "tool": {"id": "t", "name": "x", "arguments": {}}},
        {"type": "done"},
    ]


def test_sse_writer_emits_idle_heartbeat() -> None:
    writer = RecordingWriter()
    session = _SSESession(writer, flush_interval=0.02, heartbeat_interval=0.05)
    session.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and b": ping\n\n" not in writer.chunks:
        time.sleep(0.01)
    session.abort()

    assert b": ping\n\n" in writer.chunks


def test_sse_writer_disconnect_cancels_turn() -> None:
    disconnected = threading.Event()
    transport_closed = threading.Event()
    session = _SSESession(
        FailingWriter(),
        on_disconnect=disconnected.set,
        close_connection=transport_closed.set,
    )
    session.start()
    assert session.submit(TurnEvent(type="context", data={}))

    assert disconnected.wait(1.0)
    assert transport_closed.is_set()
    assert session.disconnected
    session.abort()


def test_sse_writer_disconnects_when_queue_is_full() -> None:
    entered = threading.Event()
    released = threading.Event()
    disconnected = threading.Event()

    class BlockingWriter:
        def write(self, data: bytes) -> None:
            entered.set()
            released.wait(1.0)

        def flush(self) -> None:
            return None

    session = _SSESession(
        BlockingWriter(),
        on_disconnect=disconnected.set,
        close_connection=released.set,
        max_queue=2,
    )
    session.start()
    assert session.submit(TurnEvent(type="context", data={}))
    assert entered.wait(1.0)
    assert session.submit(TurnEvent(type="context", data={}))
    assert session.submit(TurnEvent(type="context", data={}))
    assert not session.submit(TurnEvent(type="context", data={}))

    assert disconnected.wait(1.0)
    session.abort()


def test_web_approver_cancel_interrupts_wait() -> None:
    async def scenario() -> None:
        approver = WebApprover()
        request = ApprovalRequest(
            tool_call=ToolCall(id="call", name="run_shell", arguments={}),
            summary="run",
        )
        task = asyncio.create_task(approver(request))
        for _ in range(20):
            if approver.pending:
                break
            await asyncio.sleep(0.01)
        approver.cancel()
        decision = await asyncio.wait_for(task, timeout=0.5)
        assert decision.action == "deny"

    asyncio.run(scenario())


def test_cache_policy_distinguishes_html_and_assets() -> None:
    assert _cache_control("/") == "no-cache"
    assert _cache_control("/index.html") == "no-cache"
    assert _cache_control("/assets/index-abc123.js") == (
        "public, max-age=31536000, immutable"
    )


def test_live_stream_keeps_sse_event_contract(tmp_path, monkeypatch) -> None:
    import spark.web.server as server_module

    monkeypatch.setattr(server_module, "default_home", lambda: tmp_path / "home")
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    cfg.agent.approval = "full-auto"
    store = SessionStore(tmp_path / "web.db")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    thread = threading.Thread(
        target=serve_web,
        kwargs={
            "workdir": tmp_path,
            "cfg": cfg,
            "store": store,
            "host": "127.0.0.1",
            "port": port,
        },
        daemon=True,
    )
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/status", timeout=2):
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    else:
        store.close()
        raise AssertionError("server did not start")

    with urllib.request.urlopen(base + "/", timeout=5) as response:
        assert response.headers["Cache-Control"] == "no-cache"
    with urllib.request.urlopen(base + "/index.html", timeout=5) as response:
        assert response.headers["Cache-Control"] == "no-cache"
    ui = ui_dir_for()
    assert ui is not None
    asset = next((ui / "assets").iterdir())
    asset_path = "/" + asset.relative_to(ui).as_posix()
    with urllib.request.urlopen(base + asset_path, timeout=5) as response:
        assert response.headers["Cache-Control"] == (
            "public, max-age=31536000, immutable"
        )

    request = urllib.request.Request(
        base + "/api/chat/stream",
        data=json.dumps({"prompt": "hello"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
    finally:
        store.close()

    events = [
        json.loads(frame[6:])
        for frame in raw.split("\n\n")
        if frame.startswith("data: ")
    ]
    assert [event["type"] for event in events] == [
        "context",
        "text_delta",
        "context",
        "turn_end",
        "done",
    ]
    assert events[1]["text"]
