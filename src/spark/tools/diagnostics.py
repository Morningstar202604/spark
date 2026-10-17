"""P1：诊断反馈工具——编辑后把 lint/type 诊断喂回上下文（SWE-agent ACI 范式）。

不做完整 LSP 客户端（语言服务器依赖因环境而异），先内置两类零配置诊断源：
- Python：``ruff check --output-format json``（项目自身即依赖 ruff）
- TS/JS：``tsc --noEmit``（检测到 workdir 有 tsconfig 且 npx 可用时才跑）

设计：只读工具；路径默认整个 workdir；输出结构化（file/line/col/code/message），
由 AgentLoop 在编辑后主动调用或在提示词中引导模型调用，形成"改完即诊断"闭环。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox

MAX_TIMEOUT = 60
MAX_DIAGNOSTICS = 60


class DiagnosticsArgs(BaseModel):
    paths: list[str] = Field(default_factory=list, description="限定检查的相对路径，留空=整个 workdir")
    python: bool = Field(default=True, description="运行 ruff（python）诊断")
    typescript: bool = Field(default=True, description="运行 tsc（若可用）诊断")


def _clip_extras(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return items[:MAX_DIAGNOSTICS]


def _ruff_diagnostics(root: Path, targets: list[str]) -> list[dict[str, Any]]:
    # PATH 里的 ruff 优先；否则用当前解释器 -m ruff（同 venv 安装时最稳）
    ruff = shutil.which("ruff")
    cmd = [ruff, "check"] if ruff else [sys.executable, "-m", "ruff", "check"]
    cmd += ["--output-format", "json", "--no-cache", *(targets or ["."])]
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=MAX_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return []
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out: list[dict[str, Any]] = []
    for msg in raw:  # ruff JSON 为扁平结构：每项即一条诊断
        out.append(
            {
                "file": msg.get("filename", ""),
                "line": msg.get("location", {}).get("row"),
                "col": msg.get("location", {}).get("column"),
                "code": msg.get("code"),
                "message": msg.get("message", "")[:200],
                "severity": msg.get("severity"),
                "source": "ruff",
            }
        )
    return out


def sys_exec_prefix() -> str:
    import sys

    return sys.exec_prefix


def _tsc_diagnostics(root: Path) -> list[dict[str, Any]]:
    if not (root / "tsconfig.json").exists() or not shutil.which("npx"):
        return []
    try:
        proc = subprocess.run(
            ["npx", "--no-install", "tsc", "--noEmit", "--pretty", "false"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=MAX_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    out: list[dict[str, Any]] = []
    # tsc 人类可读输出：file(line,col): error TSxxxx: message
    for line in (proc.stdout or "").splitlines():
        if " error TS" not in line and " error " not in line:
            continue
        head, _, msg = line.partition(": error ")
        if "(" in head and head.endswith(")"):
            f, _, pos = head[:-1].rpartition("(")
            row, _, col = pos.partition(",")
        else:
            f, pos = head.rsplit(":", 1) if head.count(":") >= 1 else (head, "")
            row, _, col = pos.partition(":")
        code = ""
        if msg.startswith("TS"):
            code, _, msg = msg.partition(": ")
        out.append(
            {
                "file": f.strip(),
                "line": int(row) if row.strip().isdigit() else None,
                "col": int(col) if col.strip().isdigit() else None,
                "code": code.strip(),
                "message": msg.strip()[:200],
                "source": "tsc",
            }
        )
    return out


def diagnostics_tool(
    sandbox: WorkdirSandbox, args: DiagnosticsArgs
) -> ToolResult:
    root = sandbox.root
    targets = [str(sandbox.resolve(p).relative_to(root)) for p in args.paths] if args.paths else []
    diagnostics: list[dict[str, Any]] = []
    if args.python:
        diagnostics += _ruff_diagnostics(root, targets)
    if args.typescript and not targets:
        diagnostics += _tsc_diagnostics(root)
    diagnostics = _clip_extras(diagnostics)
    return ToolResult(
        ok=True,
        payload={
            "diagnostics": diagnostics,
            "count": len(diagnostics),
            "hint": (
                "修复以上诊断后可再次运行 diagnostics 验证；无诊断即通过。"
                if diagnostics
                else "当前无诊断问题。"
            ),
        },
    )


def diagnostics_diff_hint(before: int, after: int) -> str:
    """编辑前后诊断数的提示文案（供 loop/前端复用）。"""
    if after == 0:
        return "diagnostics clean"
    if after < before:
        return f"diagnostics improved: {before} -> {after}"
    if after > before:
        return f"diagnostics regressed: {before} -> {after}"
    return f"diagnostics unchanged: {after}"
