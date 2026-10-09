"""End-to-end user simulation for Spark.
Tests: CLI lifecycle, tool execution, session persistence, memory, checkpoints, error recovery.
"""
import asyncio
import os
import sys
import json
import tempfile
from pathlib import Path

# Force disable LangGraph for deterministic testing
os.environ["SPARK_USE_LANGGRAPH"] = "0"
# NOTE: Do NOT set SPARK_PROVIDER / SPARK_AGENT(*) / SPARK_MEMORY(*) env vars
# because pydantic-settings with env_prefix="SPARK_" + env_nested_delimiter="__"
# will try to parse them as nested BaseModel dicts and fail.

# Make src importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from spark.config import load_config, ensure_home, SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ApprovalDecision, ApprovalRequest, ChatDelta, ToolCall
from spark.providers.factory import create_provider
from spark.providers.mock import MockProvider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

WORKDIR = Path(__file__).resolve().parent
PASS = 0
FAIL = 0
RESULTS = []


def report(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    status = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    RESULTS.append((name, ok, detail))
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


async def always_allow(req: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(tool_call_id=req.tool_call.id, action="allow")


async def always_deny(req: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(tool_call_id=req.tool_call.id, action="deny")


def setup():
    """Build the shared objects (cfg, store, registry, sandbox)."""
    # Isolate from global config to avoid pollution between tests
    cfg = SparkConfig(
        provider=__import__("spark.config", fromlist=["ProviderConfig"]).ProviderConfig(name="mock"),
        agent=__import__("spark.config", fromlist=["AgentConfig"]).AgentConfig(approval="auto-edit"),
    )
    store = SessionStore(":memory:")
    session_id = store.create_session(WORKDIR, "mock", title="e2e test")
    provider = MockProvider(rounds=[
        # Round 1: respond with a list_dir tool call
        [ChatDelta(type="text", text="Let me check the files."),
         ChatDelta(type="tool_call", tool_call=ToolCall(id="call_1", name="list_dir", arguments={"path": "."}))],
        # Round 2: respond with a final message (no tool calls)
        [ChatDelta(type="text", text="Here are the files."),
         ChatDelta(type="text", text=" Done!")],
    ])
    sandbox = WorkdirSandbox(WORKDIR, cfg)
    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    memory = None  # skip memory for core loop test
    return cfg, store, session_id, provider, registry, memory


# ────────────────────────────────────────────────────────────
# Test 1: Basic chat with tool execution
# ────────────────────────────────────────────────────────────
async def test_basic_chat_with_tool():
    name = "Test 1: Basic chat (text → tool → final)"
    try:
        cfg, store, sid, provider, registry, memory = setup()
        loop = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider,
            registry=registry, store=store, session_id=sid,
            approver=always_allow, memory=memory,
        )
        events = []
        async for event in loop.iter_turn("What files are here?"):
            events.append(event)
        # Should have: context, text_delta, tool_start, tool_end, context, turn_end
        types = [e.type for e in events]
        has_text = any(t == "text_delta" for t in types)
        has_tool = any(t == "tool_start" for t in types)
        has_end = types[-1] == "turn_end" if types else False
        report(name, has_text and has_tool and has_end,
               f"events: {types}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 2: Multiple tool calls in one turn
# ────────────────────────────────────────────────────────────
async def test_multiple_tool_rounds():
    name = "Test 2: Multi-round tools (sequential)"
    try:
        cfg, store, sid, _, registry, memory = setup()
        provider = MockProvider(rounds=[
            # Round 1: list dir
            [ChatDelta(type="text", text="Checking..."),
             ChatDelta(type="tool_call", tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}))],
            # Round 2: read a file
            [ChatDelta(type="text", text="Reading..."),
             ChatDelta(type="tool_call", tool_call=ToolCall(id="c2", name="read_file", arguments={"path": "e2e_test.py"}))],
            # Round 3: final
            [ChatDelta(type="text", text="All done!")],
        ])
        loop = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider,
            registry=registry, store=store, session_id=sid,
            approver=always_allow, memory=memory,
        )
        events = []
        async for event in loop.iter_turn("Read the test file"):
            events.append(event)
        tool_starts = [e for e in events if e.type == "tool_start"]
        tool_ends = [e for e in events if e.type == "tool_end"]
        report(name, len(tool_starts) >= 2 and len(tool_ends) >= 2,
               f"tool_starts={len(tool_starts)}, tool_ends={len(tool_ends)}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 3: Approval deny → error recovery
# ────────────────────────────────────────────────────────────
async def test_approval_deny():
    name = "Test 3: Approval deny flow"
    try:
        cfg, store, sid, _, registry, memory = setup()
        cfg.agent.approval = "suggest"
        provider = MockProvider(rounds=[
            [ChatDelta(type="text", text="Trying to write..."),
             ChatDelta(type="tool_call", tool_call=ToolCall(id="c1", name="write_file", arguments={"path": "test.txt", "content": "hello"}))],
        ])
        loop = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider,
            registry=registry, store=store, session_id=sid,
            approver=always_deny, memory=memory,
        )
        events = []
        async for event in loop.iter_turn("Write a file"):
            events.append(event)
        # Should get tool_end with denied result
        tool_ends = [e for e in events if e.type == "tool_end"]
        denied = any(e.result and not getattr(e.result, "ok", True)
                     for e in tool_ends if e.result and hasattr(e.result, "ok"))
        report(name, len(tool_ends) > 0 and denied,
               f"tool_ends={len(tool_ends)}, denied={denied}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 4: Session persistence across turns
# ────────────────────────────────────────────────────────────
async def test_session_persistence():
    name = "Test 4: Session persistence (history survives across turns)"
    try:
        cfg, store, sid, _, registry, memory = setup()

        # Turn 1
        provider1 = MockProvider(rounds=[
            [ChatDelta(type="text", text="Hello!"),
             ChatDelta(type="tool_call", tool_call=ToolCall(id="c1", name="list_dir", arguments={"path": "."}))],
        ])
        loop1 = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider1,
            registry=registry, store=store, session_id=sid,
            approver=always_allow, memory=memory,
        )
        async for event in loop1.iter_turn("Hi"):
            pass

        # Turn 2 (new loop, same session)
        provider2 = MockProvider(rounds=[
            [ChatDelta(type="text", text="Follow-up response.")],
        ])
        loop2 = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider2,
            registry=registry, store=store, session_id=sid,
            approver=always_allow, memory=memory,
        )
        events2 = []
        async for event in loop2.iter_turn("Continue"):
            events2.append(event)

        # Check that history contains both turns
        msgs = store.load_messages(sid)
        user_msgs = [m for m in msgs if m.role == "user"]
        report(name, len(user_msgs) >= 2,
               f"stored user messages: {len(user_msgs)}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 5: Memory extraction & retrieval
# ────────────────────────────────────────────────────────────
async def test_memory_store():
    name = "Test 5: Memory store (ChromaDB) CRUD + search"
    try:
        from spark.memory.store import MemoryStore
        store = MemoryStore(":memory:")
        mid = store.add("Python is the preferred language", type="preference",
                        importance=8.0)
        mid2 = store.add("Project uses FastAPI", type="project",
                         importance=9.0)
        # Search
        results = store.search("python language", top_k=5)
        found = any(r[0].id == mid for r in results)
        # Archive and verify
        store.archive(mid2)
        row_after = store.get(mid2)
        archived_ok = row_after is None or row_after.status == "archived"
        results2 = store.search("FastAPI", top_k=5)
        found2 = any(r[0].id == mid2 for r in results2)
        stats = store.stats()
        report(name, found and archived_ok and not found2,
               f"search_found={found}, archived_ok={archived_ok}, archive_hidden={not found2}, stats={stats}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 6: Memory embeddings with fallback
# ────────────────────────────────────────────────────────────
async def test_memory_embeddings():
    name = "Test 6: Memory embeddings (with fallback)"
    try:
        from spark.memory.store import MemoryStore
        store = MemoryStore(":memory:")
        mid = store.add("Machine learning model", type="general",
                        importance=7.0,
                        embedding=[0.1] * 1536,
                        embedding_model="text-embedding-3-small")
        # Search with embedding
        results = store.search("neural network", top_k=3,
                               query_embedding=[0.1] * 1536)
        # Search without embedding (keyword fallback)
        results2 = store.search("machine learning", top_k=3)
        report(name, len(results) > 0 and len(results2) > 0,
               f"vec_search={len(results)}, kw_search={len(results2)}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 7: Checkpoint creation
# ────────────────────────────────────────────────────────────
async def test_checkpoint():
    name = "Test 7: Checkpoint creation & rollback"
    tmpdir = None
    try:
        from spark.core.checkpoints import make_checkpoint_record, restore_workdir
        import tempfile, os
        tmpdir = Path(tempfile.mkdtemp())
        # Create a test file
        (tmpdir / "hello.txt").write_text("original content")
        # Create checkpoint
        snap_id, file_hash = make_checkpoint_record(tmpdir)
        # Modify file
        (tmpdir / "hello.txt").write_text("modified content")
        # Restore
        restore_workdir(tmpdir, snap_id)
        content = (tmpdir / "hello.txt").read_text()
        report(name, content == "original content",
               f"content_after_restore='{content}'")
    except Exception as e:
        report(name, False, f"Exception: {e}")
    finally:
        if tmpdir is not None:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ────────────────────────────────────────────────────────────
# Test 8: Compaction (LlamaIndex ChatSummaryMemoryBuffer)
# ────────────────────────────────────────────────────────────
async def test_compaction():
    name = "Test 8: Auto-compaction (LlamaIndex)"
    try:
        cfg, store, sid, _, registry, memory = setup()
        # Force small context to trigger compaction
        cfg.context.max_context_tokens = 500
        cfg.context.compact_threshold = 0.5
        cfg.context.keep_recent_messages = 2

        # Add many messages to trigger compaction
        for i in range(10):
            from spark.models import ChatMessage
            store.append_message(sid, ChatMessage(role="user", content=f"Message " + "x" * 100 + f" {i}"))
            store.append_message(sid, ChatMessage(role="assistant", content=f"Reply " + "y" * 100 + f" {i}"))

        provider = MockProvider(rounds=[
            [ChatDelta(type="text", text="Final answer.")],
        ])
        loop = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider,
            registry=registry, store=store, session_id=sid,
            approver=always_allow, memory=memory,
        )
        events = []
        async for event in loop.iter_turn("Test"):
            events.append(event)
        compaction_events = [e for e in events if e.type == "compaction"]
        report(name, len(compaction_events) > 0,
               f"compaction_triggered={len(compaction_events) > 0}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 9: Config env-var override (pydantic-settings)
# ────────────────────────────────────────────────────────────
async def test_config_env_override():
    name = "Test 9: Config env-var override (pydantic-settings)"
    try:
        os.environ["SPARK_AGENT__MAX_TOOL_ROUNDS"] = "99"
        os.environ["SPARK_MEMORY__TOP_K"] = "42"
        cfg = SparkConfig()
        ok1 = cfg.agent.max_tool_rounds == 99
        ok2 = cfg.memory.top_k == 42
        report(name, ok1 and ok2,
               f"max_tool_rounds={cfg.agent.max_tool_rounds}, top_k={cfg.memory.top_k}")
    except Exception as e:
        report(name, False, f"Exception: {e}")
    finally:
        os.environ.pop("SPARK_AGENT__MAX_TOOL_ROUNDS", None)
        os.environ.pop("SPARK_MEMORY__TOP_K", None)


# ────────────────────────────────────────────────────────────
# Test 10: Provider factory (LiteLLM)
# ────────────────────────────────────────────────────────────
async def test_provider_factory():
    name = "Test 10: Provider factory (LiteLLM integration)"
    try:
        cfg = SparkConfig()
        cfg.provider.name = "mock"
        provider = create_provider(cfg, mock=MockProvider())
        ok_mock = provider is not None

        cfg2 = SparkConfig()
        cfg2.provider.name = "ollama"
        cfg2.provider.base_url = "http://127.0.0.1:11434/v1"
        provider2 = create_provider(cfg2)
        ok_ollama = "Ollama" in type(provider2).__name__
        report(name, ok_mock and ok_ollama,
               f"mock={type(provider).__name__}, ollama={type(provider2).__name__}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 11: Circuit breaker
# ────────────────────────────────────────────────────────────
async def test_circuit_breaker():
    name = "Test 11: Circuit breaker (repeat detection)"
    try:
        cfg, store, sid, _, registry, memory = setup()
        cfg.agent.max_repeat_calls = 2  # trip after 2 repeats
        TC = ToolCall
        # 3 rounds with same tool call → should trip breaker
        provider = MockProvider(rounds=[
            [ChatDelta(type="tool_call", tool_call=TC(id="c1", name="run_shell", arguments={"cmd": "echo hi"}))],
            [ChatDelta(type="tool_call", tool_call=TC(id="c2", name="run_shell", arguments={"cmd": "echo hi"}))],
            [ChatDelta(type="tool_call", tool_call=TC(id="c3", name="run_shell", arguments={"cmd": "echo hi"}))],
        ])
        loop = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider,
            registry=registry, store=store, session_id=sid,
            approver=always_allow, memory=memory,
        )
        events = []
        async for event in loop.iter_turn("Run"):
            events.append(event)
        cb_events = [e for e in events if e.type == "context" and e.data and "circuit_breaker" in str(e.data)]
        report(name, len(cb_events) > 0,
               f"circuit_breaker_events={len(cb_events)}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 12: Cancellation
# ────────────────────────────────────────────────────────────
async def test_cancellation():
    name = "Test 12: Turn cancellation"
    try:
        cfg, store, sid, _, registry, memory = setup()
        TC = ToolCall
        provider = MockProvider(rounds=[
            [ChatDelta(type="tool_call", tool_call=TC(id="c1", name="list_dir", arguments={"path": "."}))],
            [ChatDelta(type="tool_call", tool_call=TC(id="c2", name="list_dir", arguments={"path": "."}))],
        ])
        loop = AgentLoop(
            workdir=WORKDIR, cfg=cfg, provider=provider,
            registry=registry, store=store, session_id=sid,
            approver=always_allow, memory=memory,
        )
        events = []
        gen = loop.iter_turn("Cancel test")
        # Get first event then cancel
        try:
            first = await gen.__anext__()
            events.append(first)
            loop.cancel()
            async for event in gen:
                events.append(event)
        except StopAsyncIteration:
            pass
        error_events = [e for e in events if e.type == "turn_error" and "cancel" in str(e.text).lower()]
        report(name, len(error_events) > 0,
               f"events={[e.type for e in events]}, error_events={len(error_events)}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Test 13: Sandbox filesystem enforcement
# ────────────────────────────────────────────────────────────
async def test_sandbox():
    name = "Test 13: Sandbox FS enforcement (escaping workdir blocked)"
    try:
        cfg = SparkConfig()
        cfg.agent.approval = "auto-edit"
        sandbox = WorkdirSandbox(WORKDIR, cfg)
        # Try to access parent (should be rejected)
        err_parent = sandbox.check_write_path(Path("/etc/passwd"))
        err_local = sandbox.check_write_path(WORKDIR / "e2e_test.py")
        report(name, err_parent is not None and err_local is None,
               f"parent_blocked={err_parent is not None}, local_allowed={err_local is None}")
    except Exception as e:
        report(name, False, f"Exception: {e}")


# ────────────────────────────────────────────────────────────
# Main runner
# ────────────────────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("Spark End-to-End User Simulation Test Suite")
    print("=" * 60)

    tests = [
        test_basic_chat_with_tool,
        test_multiple_tool_rounds,
        test_approval_deny,
        test_session_persistence,
        test_memory_store,
        test_memory_embeddings,
        test_checkpoint,
        test_compaction,
        test_config_env_override,
        test_provider_factory,
        test_circuit_breaker,
        test_cancellation,
        test_sandbox,
    ]

    for test_fn in tests:
        await test_fn()

    print()
    print("=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
    print("=" * 60)
    if FAIL > 0:
        print("\nFailed tests:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}: {detail}")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
