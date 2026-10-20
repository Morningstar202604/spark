"""真机端到端：让真实模型完成一次"读代码→改代码→跑测试"的完整闭环。

与 live_smoke 的区别：这里断言的是**代码真的被改对了**，而不只是工具被调用过。
"""
import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spark.config import SparkConfig
from spark.core.loop import AgentLoop
from spark.models import ApprovalDecision
from spark.providers.factory import create_provider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.registry import ToolContext, ToolRegistry

BUGGY = '''def add(a, b):
    """Return the sum of a and b."""
    return a - b
'''
TEST = '''from calc import add


def test_add_positive():
    assert add(2, 3) == 5


def test_add_negative():
    assert add(-1, -2) == -3
'''


def make_workdir() -> Path:
    work = Path(tempfile.mkdtemp(prefix="spark-e2e-"))
    (work / "calc.py").write_text(BUGGY, encoding="utf-8")
    (work / "test_calc.py").write_text(TEST, encoding="utf-8")
    return work


def run_pytest(work: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "test_calc.py", "-q"],
        cwd=work, capture_output=True, text=True, timeout=120,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-600:]


async def main() -> int:
    base = os.environ["SPARK_TEST_BASE_URL"]
    key = os.environ["SPARK_TEST_API_KEY"]
    model = os.environ["SPARK_TEST_MODEL"]

    work = make_workdir()
    code, out = run_pytest(work)
    print(f"[baseline] pytest exit={code} (expected non-zero: the bug is present)")
    assert code != 0, "fixture is not actually broken; test would prove nothing"

    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.base_url = base
    cfg.provider.model = model
    cfg.provider.api_key = key
    cfg.agent.approval = "auto-edit"
    cfg.agent.max_tool_rounds = 20
    cfg.agent.sandbox_mode = "workspace"

    approvals: list[str] = []

    async def approver(request):
        """Approve the test/shell steps; auto-edit already allows writes.

        `run_tests` deliberately requires approval (it executes repo-supplied
        code), so a headless run must supply a decision or the tool is denied
        with "no approver" — which is what the first run reported as "blocked".
        """
        approvals.append(request.tool_call.name)
        return ApprovalDecision(tool_call_id=request.tool_call.id, action="allow")

    store = SessionStore(work / "sessions.db")
    sid = store.create_session(work, model)
    ctx = ToolContext(sandbox=WorkdirSandbox(work, cfg), config=cfg)
    loop = AgentLoop(
        workdir=work, cfg=cfg, provider=create_provider(cfg),
        registry=ToolRegistry(ctx), store=store, session_id=sid,
        approver=approver,
    )

    prompt = (
        "The file calc.py has a bug: add() subtracts instead of adding, and the tests in "
        "test_calc.py fail. Read the files, fix add() so it returns the sum, then run the "
        "tests with the run_tests tool and report the result. Do not modify the tests."
    )
    tools_used: list[str] = []
    final = ""
    async def drive():
        nonlocal final
        async for ev in loop.iter_turn(prompt):
            if ev.type == "tool_start" and ev.tool_call:
                tools_used.append(ev.tool_call.name)
            elif ev.type == "turn_end" and ev.text:
                final = ev.text
            elif ev.type == "turn_error":
                print("turn_error:", ev.text)
    await asyncio.wait_for(drive(), timeout=420)

    print(f"[tools] {tools_used}")
    print(f"[approved] {approvals}")
    print(f"[final] {final[:300]!r}")

    fixed = (work / "calc.py").read_text(encoding="utf-8")
    test_untouched = (work / "test_calc.py").read_text(encoding="utf-8") == TEST
    code, out = run_pytest(work)
    print(f"[after] pytest exit={code}")
    print(out.strip().splitlines()[-3:] if out.strip() else "")

    checks = {
        "used read_file": "read_file" in tools_used,
        "used a write tool": any(t in tools_used for t in ("apply_edit", "apply_patch", "write_file")),
        "used run_tests": "run_tests" in tools_used,
        "tests now pass": code == 0,
        "tests file untouched": test_untouched,
        "add() returns sum": "a + b" in fixed or "a+b" in fixed.replace(" ", ""),
    }
    for name, ok in checks.items():
        print(("[PASS] " if ok else "[FAIL] ") + name)
    print(f"\nRESULT: {sum(checks.values())}/{len(checks)}")
    return 0 if all(checks.values()) else 1


sys.exit(asyncio.run(main()))
