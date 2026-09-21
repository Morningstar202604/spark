from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from pydantic import BaseModel

from spark.models import ToolResult
from spark.sandbox import WorkdirSandbox

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", ".next", ".nuxt", ".cache", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "vendor",
}

DEFAULT_SKIP_PATTERN = ""


class GrepArgs(BaseModel):
    pattern: str
    path: str = "."
    glob: str = ""  # e.g. "*.py"
    max_results: int = 50


class GlobArgs(BaseModel):
    pattern: str  # e.g. "src/**/*.ts"
    path: str = "."
    max_results: int = 200


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git")]
        yield Path(dirpath), filenames


def grep_tool(sandbox: WorkdirSandbox, args: GrepArgs) -> ToolResult:
    try:
        base = sandbox.resolve(args.path)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not base.exists():
        return ToolResult(ok=False, payload={"error": f"path not found: {args.path}"})
    try:
        regex = re.compile(args.pattern)
    except re.error as exc:
        return ToolResult(ok=False, payload={"error": f"invalid regex: {exc}"})
    glob_filter = args.glob.strip()
    max_results = max(1, min(200, args.max_results))
    matches: list[str] = []
    truncated = False
    for dirpath, filenames in _walk(base):
        if truncated:
            break
        for name in filenames:
            if len(matches) >= max_results:
                truncated = True
                break
            if glob_filter and not fnmatch.fnmatch(name, glob_filter):
                continue
            file = dirpath / name
            try:
                if file.stat().st_size > 1_500_000:
                    continue
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = file.relative_to(sandbox.root).as_posix()
            for lineno, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{rel}:{lineno}: {line.strip()[:240]}")
                    if len(matches) >= max_results:
                        truncated = True
                        break
    if not matches:
        return ToolResult(ok=True, payload={"results": [], "count": 0, "note": "no matches"})
    payload: dict = {"results": matches, "count": len(matches)}
    if truncated:
        payload["note"] = f"truncated at {max_results} matches; narrow the search if needed"
    return ToolResult(ok=True, payload=payload)


def glob_tool(sandbox: WorkdirSandbox, args: GlobArgs) -> ToolResult:
    try:
        base = sandbox.resolve(args.path)
    except Exception as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not base.exists():
        return ToolResult(ok=False, payload={"error": f"path not found: {args.path}"})
    pattern = args.pattern.strip().lstrip("/")
    collapsed = pattern.replace("**/", "")
    max_results = max(1, min(500, args.max_results))
    out: list[str] = []
    truncated = False
    for dirpath, filenames in _walk(base):
        if truncated:
            break
        for name in filenames:
            file = dirpath / name
            rel = file.relative_to(base).as_posix()
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, collapsed) or fnmatch.fnmatch(name, pattern):
                out.append(file.relative_to(sandbox.root).as_posix())
                if len(out) >= max_results:
                    truncated = True
                    break
    if not out:
        return ToolResult(ok=True, payload={"files": [], "count": 0, "note": "no files matched"})
    payload: dict = {"files": out, "count": len(out)}
    if truncated:
        payload["note"] = f"truncated at {max_results} files"
    return ToolResult(ok=True, payload=payload)
