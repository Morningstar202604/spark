"""定点变异测试：验证安全/核心逻辑的测试是否真的能抓到缺陷。

思路：对被测函数做一次等价于"写出 bug"的改动，跑相关测试。
  - 测试失败 → 该行为被真实覆盖（KILLED）
  - 测试仍通过 → 覆盖是假的（SURVIVED）

判据必须严格：变异后行为必须真的改变，否则 SURVIVED 毫无信息量。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORK = Path(__file__).resolve().parents[1] / ".."   # repo root -> agent/spark
REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "spark"
PY_EXE = sys.executable

MUTANTS: list[dict] = [
    {
        "name": "M1 受保护路径比对恒不命中",
        "file": "sandbox.py",
        "old": "        def hit_by(candidate: Path) -> str | None:",
        "new": "        def hit_by(candidate: Path) -> str | None:\n            return None",
        "tests": ["tests/test_hardening_regressions.py", "tests/test_sandbox_policy.py"],
    },
    {
        "name": "M2 去掉引号剥离",
        "file": "sandbox.py",
        "old": '    return token.replace(\'"\', "").replace("\'", "").strip()',
        "new": "    return token.strip()",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M3 去掉 IFS 清理",
        "file": "sandbox.py",
        "old": '        cleaned = cleaned.replace("${IFS}", " ").replace("$IFS", " ")',
        "new": "        cleaned = cleaned",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M4 去掉嵌套 .git 保护",
        "file": "sandbox.py",
        "old": '        hit = _git_control_surface(path, self.root)\n        if hit:\n            raise SandboxPolicyError(f"Blocked protected path: {hit}")',
        "new": "        hit = None",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M5 放行 '-' 开头的 diagnostics target",
        "file": "tools/diagnostics.py",
        "old": '        if not text or text.startswith("-"):\n            return None',
        "new": "        if not text:\n            return None",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M6 store.touch 走 title 分支（NOT NULL）",
        "file": "store.py",
        "old": "            if title is None:",
        "new": "            if False:",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M7 delete_session 不删 FTS",
        "file": "store.py",
        "old": '"DELETE FROM messages_fts WHERE rowid IN "',
        "new": '"DELETE FROM messages_fts WHERE 0 AND rowid IN "',
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M8 审批缓存退回 id-only",
        "file": "core/loop.py",
        "old": '                {"id": call.id, "name": call.name, "arguments": call.arguments},',
        "new": '                {"id": call.id},',
        "tests": ["tests/test_agent_runtime.py"],
    },
    {
        "name": "M9 去破坏性前缀匹配",
        "file": "sandbox.py",
        "old": "        if needle in _PREFIX_PATTERNS:\n            if needle in variant:\n                return True\n            continue",
        "new": "        if needle in _PREFIX_PATTERNS:\n            continue",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M10 不展开环境变量",
        "file": "sandbox.py",
        "old": "        expanded = _expand_env_indirection(command)\n        for source in {command, expanded}:",
        "new": "        expanded = command\n        for source in {command, expanded}:",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M11 cwd 相对路径不参与比对",
        "file": "sandbox.py",
        "old": "        if cwd is not None and cwd != self.root:\n            bases_for_relative.append(cwd)",
        "new": "        if False:\n            bases_for_relative.append(cwd)",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M12 上下文设置不校验就写入",
        "file": "web/_handlers_settings.py",
        "old": '        except (TypeError, ValueError) as exc:\n            handler._json(400, {"error": f"invalid context setting: {exc}"})\n            return',
        "new": "        except (TypeError, ValueError):\n            pass",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M13 重定向不再拆分",
        "file": "sandbox.py",
        "old": '    return re.sub(r"\\s*(\\d?&?>>?|\\d?<)\\s*", r" \\1 ", command)',
        "new": "    return command",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M14a git alias 快速分支不再拦截",
        "file": "sandbox.py",
        "old": '        if low.startswith("-calias.") or low.startswith("-c") and "alias." in low:\n            return "command blocked: git alias definitions can execute arbitrary commands"',
        "new": "        if False:\n            return None",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M14b git alias 分离参数分支不再拦截",
        "file": "sandbox.py",
        "old": '        if low in {"-c", "--config-env"} and "alias." in " ".join(tokens).lower():\n            return "command blocked: git alias definitions can execute arbitrary commands"',
        "new": "        if False:\n            return None",
        "tests": ["tests/test_hardening_regressions.py"],
    },
    {
        "name": "M15 设备汇不再豁免（/dev/null 误杀回归）",
        "file": "sandbox.py",
        "old": "            if _is_device_sink(candidate):\n                return None",
        "new": "            if False:\n                return None",
        "tests": ["tests/test_hardening_regressions.py"],
    },
]


def run_tests(files: list[str]) -> tuple[bool, str]:
    proc = subprocess.run(
        [PY_EXE, "-m", "pytest", *files, "-x", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "SPARK_HOME": str(REPO / ".mutation-home")},
    )
    return proc.returncode == 0, (proc.stdout or "")[-500:]


def main() -> int:
    results = []
    for m in MUTANTS:
        target = SRC / m["file"]
        original = target.read_text(encoding="utf-8")
        if m["old"] not in original:
            results.append({"name": m["name"], "verdict": "PATTERN_MISSING", "tail": ""})
            print(f"[SKIP] {m['name']}: pattern not found")
            continue
        mutated = original.replace(m["old"], m["new"], 1)
        if mutated == original:
            results.append({"name": m["name"], "verdict": "NO_CHANGE", "tail": ""})
            print(f"[SKIP] {m['name']}: replacement was a no-op")
            continue
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copyfile(target, backup)
        try:
            target.write_text(mutated, encoding="utf-8")
            passed, tail = run_tests(m["tests"])
            verdict = "SURVIVED" if passed else "KILLED"
            results.append({"name": m["name"], "verdict": verdict, "tail": tail})
            print(f"[{verdict}] {m['name']}")
        except subprocess.TimeoutExpired:
            results.append({"name": m["name"], "verdict": "TIMEOUT", "tail": ""})
            print(f"[TIMEOUT] {m['name']}")
        finally:
            shutil.copyfile(backup, target)
            backup.unlink()

    killed = sum(1 for r in results if r["verdict"] == "KILLED")
    survived = [r for r in results if r["verdict"] == "SURVIVED"]
    missing = [r for r in results if r["verdict"] in ("PATTERN_MISSING", "NO_CHANGE")]
    print(f"\n{killed}/{len(results)} mutants killed | survived {len(survived)} | skipped {len(missing)}")
    for r in survived:
        print(f"  SURVIVED: {r['name']}")
        print(f"            {r['tail'].strip().splitlines()[-1] if r['tail'] else ''}")
    for r in missing:
        print(f"  {r['verdict']}: {r['name']}")
    json.dump(results, open(REPO / ".mutation-results.json", "w"), ensure_ascii=False, indent=1)
    return 0 if not survived else 1


if __name__ == "__main__":
    sys.exit(main())
