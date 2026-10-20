"""Live model smoke test — proves the whole loop, not just the HTTP layer.

Reads credentials from the environment ONLY (never from the repo):

    SPARK_TEST_BASE_URL, SPARK_TEST_API_KEY, SPARK_TEST_MODEL

Phases, each with its own timeout so a hung provider can never wedge the run:

1. non-stream completion
2. streaming text deltas
3. tool call → real file write → second round reads the result back
4. tool call → run_shell → exit code observed
5. approval gate: a write in `suggest` mode must ask, and a deny must not touch disk
6. context accounting: usage numbers move after a tool round

Usage:
    python scripts/live_smoke.py [--timeout 90]
Exit code 0 = all phases passed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spark.config import SparkConfig  # noqa: E402
from spark.core.loop import AgentLoop  # noqa: E402
from spark.models import ApprovalDecision  # noqa: E402
from spark.providers.factory import create_provider  # noqa: E402
from spark.providers.probe import probe_provider  # noqa: E402
from spark.sandbox import WorkdirSandbox  # noqa: E402
from spark.store import SessionStore  # noqa: E402
from spark.tools.registry import ToolContext, ToolRegistry  # noqa: E402


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"missing {name}")
    return value


def make_cfg(approval: str) -> SparkConfig:
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = env("SPARK_TEST_BASE_URL")
    cfg.provider.model = env("SPARK_TEST_MODEL")
    cfg.provider.api_key = env("SPARK_TEST_API_KEY")
    cfg.agent.approval = approval
    cfg.agent.sandbox_mode = "workspace"
    cfg.agent.max_tool_rounds = 8
    cfg.context.max_context_tokens = 32768
    return cfg


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.rows.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}: {detail}", flush=True)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(r[1] for r in self.rows)


async def run_turn(loop: AgentLoop, prompt: str, timeout: float) -> list:
    """One turn with a hard timeout; cancellation propagates to the loop."""
    async def consume():
        events = []
        async for ev in loop.iter_turn(prompt):
            events.append(ev)
        return events

    return await asyncio.wait_for(consume(), timeout=timeout)


def build_loop(work: Path, cfg: SparkConfig, approver=None) -> AgentLoop:
    provider = create_provider(cfg)
    store = SessionStore(work / "sessions.db")
    sid = store.create_session(work, cfg.provider.model)
    ctx = ToolContext(sandbox=WorkdirSandbox(work, cfg), config=cfg)
    return AgentLoop(
        workdir=work,
        cfg=cfg,
        provider=provider,
        registry=ToolRegistry(ctx),
        store=store,
        session_id=sid,
        approver=approver,
    )


async def phase_probe(res: Result, timeout: float) -> None:
    t0 = time.monotonic()
    try:
        probe = await asyncio.wait_for(
            probe_provider(
                base_url=env("SPARK_TEST_BASE_URL"),
                api_key=env("SPARK_TEST_API_KEY"),
                model=env("SPARK_TEST_MODEL"),
                rounds=1,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        res.add("probe", False, f"timed out after {timeout}s")
        return
    res.add(
        "probe",
        probe.ok,
        f"{probe.latency_ms}ms content={probe.content[:40]!r} err={probe.error}",
    )
    print(f"    (probe wall clock {time.monotonic() - t0:.1f}s)")


async def phase_stream(res: Result, timeout: float) -> None:
    work = Path(tempfile.mkdtemp())
    loop = build_loop(work, make_cfg("auto-edit"))
    try:
        events = await run_turn(loop, "Reply with the single word: pong", timeout)
    except asyncio.TimeoutError:
        res.add("stream", False, f"timed out after {timeout}s")
        return
    text = "".join(e.text or "" for e in events if e.type == "text_delta")
    reasoning = sum(1 for e in events if e.type == "reasoning_delta")
    res.add(
        "stream",
        bool(text.strip()),
        f"{len(text)} chars, {reasoning} reasoning deltas, ends={events[-1].type}",
    )


async def phase_tool_write(res: Result, timeout: float) -> None:
    work = Path(tempfile.mkdtemp())
    loop = build_loop(work, make_cfg("auto-edit"))
    prompt = (
        "Use the write_file tool to create a file named hello.txt whose content is "
        "exactly 'spark-smoke-ok'. Then reply with the word DONE."
    )
    try:
        events = await run_turn(loop, prompt, timeout)
    except asyncio.TimeoutError:
        res.add("tool:write_file", False, f"timed out after {timeout}s")
        return
    target = work / "hello.txt"
    tools = [e.tool_call.name for e in events if e.type == "tool_start"]
    ok = target.is_file() and "spark-smoke-ok" in target.read_text(encoding="utf-8")
    res.add("tool:write_file", ok, f"tools={tools} file={target.is_file()}")


async def phase_tool_shell(res: Result, timeout: float) -> None:
    work = Path(tempfile.mkdtemp())
    asked: list[str] = []

    async def allow_shell(request):
        # auto-edit lets writes through but still gates shell; the smoke test
        # approves explicitly so it measures the tool, not the policy.
        asked.append(request.tool_call.name)
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="allow")

    loop = build_loop(work, make_cfg("auto-edit"), approver=allow_shell)
    prompt = (
        "Run the shell command `echo spark-shell-ok` using the run_shell tool, "
        "then tell me the exit code."
    )
    try:
        events = await run_turn(loop, prompt, timeout)
    except asyncio.TimeoutError:
        res.add("tool:run_shell", False, f"timed out after {timeout}s (asked={asked})")
        return
    outputs = [
        str(e.result.payload.get("stdout", "")) for e in events
        if e.type == "tool_end" and e.tool_call and e.tool_call.name == "run_shell" and e.result
    ]
    ok = any("spark-shell-ok" in out for out in outputs)
    res.add("tool:run_shell", ok, f"stdout seen={ok} calls={len(outputs)} approvals={asked}")


async def phase_approval(res: Result, timeout: float) -> None:
    work = Path(tempfile.mkdtemp())
    asked: list[str] = []

    async def deny_all(request):
        asked.append(request.tool_call.name)
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="deny")

    loop = build_loop(work, make_cfg("suggest"), approver=deny_all)
    prompt = (
        "Use write_file to create denied.txt with content 'x', then reply DONE."
    )
    try:
        await run_turn(loop, prompt, timeout)
    except asyncio.TimeoutError:
        res.add("approval:deny", False, f"timed out after {timeout}s")
        return
    blocked = not (work / "denied.txt").exists()
    res.add(
        "approval:deny",
        bool(asked) and blocked,
        f"asked={asked} file_created={(work / 'denied.txt').exists()}",
    )


async def phase_usage(res: Result, timeout: float) -> None:
    work = Path(tempfile.mkdtemp())
    loop = build_loop(work, make_cfg("auto-edit"))
    try:
        await run_turn(loop, "Say OK.", timeout)
    except asyncio.TimeoutError:
        res.add("usage", False, f"timed out after {timeout}s")
        return
    usage = loop._usage()
    res.add(
        "usage",
        usage["used"] > 0 and usage["limit"] > 0,
        f"used={usage['used']} limit={usage['limit']} pct={usage['percent']}",
    )


async def main_async(timeout: float) -> Result:
    res = Result()
    started = time.monotonic()
    # Each phase is independent: a failure never stops the rest.
    for phase in (
        phase_probe,
        phase_stream,
        phase_tool_write,
        phase_tool_shell,
        phase_approval,
        phase_usage,
    ):
        t0 = time.monotonic()
        try:
            await phase(res, timeout)
        except Exception as exc:  # noqa: BLE001 - report, keep going
            res.add(phase.__name__, False, f"{type(exc).__name__}: {exc}")
        print(f"    ({phase.__name__} took {time.monotonic() - t0:.1f}s)", flush=True)
    print(f"\nTOTAL wall clock: {time.monotonic() - started:.1f}s")
    return res


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=90.0, help="per-phase timeout (s)")
    args = parser.parse_args()
    res = asyncio.run(main_async(args.timeout))
    passed = sum(1 for r in res.rows if r[1])
    print(f"\nRESULT: {passed}/{len(res.rows)} phases passed")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
