from __future__ import annotations

from pydantic import BaseModel

from spark.errors import PathEscapeError
from spark.models import ToolResult
from spark.sandbox import SandboxPolicyError, WorkdirSandbox


class ReadFileArgs(BaseModel):
    path: str
    offset: int | None = None
    limit: int | None = 200


class ListDirArgs(BaseModel):
    path: str = "."
    max_entries: int = 200


class WriteFileArgs(BaseModel):
    path: str
    content: str


class ApplyPatchArgs(BaseModel):
    path: str
    old_text: str
    new_text: str


def _lines(content: str, offset: int | None, limit: int | None) -> str:
    rows = content.splitlines()
    start = 1 if offset is None else max(offset, 1)
    end = len(rows) if limit is None else start - 1 + max(limit, 0)
    sliced = rows[start - 1 : end]
    return "\n".join(sliced)


def read_file(sandbox: WorkdirSandbox, args: ReadFileArgs) -> ToolResult:
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not path.exists() or not path.is_file():
        return ToolResult(ok=False, payload={"error": f"File not found: {args.path}"})
    text = path.read_text(encoding="utf-8", errors="replace")
    return ToolResult(ok=True, payload={"path": args.path, "content": _lines(text, args.offset, args.limit)})


def list_dir(sandbox: WorkdirSandbox, args: ListDirArgs) -> ToolResult:
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    if not path.exists() or not path.is_dir():
        return ToolResult(ok=False, payload={"error": f"Directory not found: {args.path}"})
    entries = []
    for child in sorted(path.iterdir(), key=lambda p: p.name)[: args.max_entries]:
        item = {"name": child.name, "type": "dir" if child.is_dir() else "file"}
        if child.is_file():
            item["size"] = child.stat().st_size
        entries.append(item)
    return ToolResult(ok=True, payload={"path": args.path, "entries": entries})


def write_file(sandbox: WorkdirSandbox, args: WriteFileArgs) -> ToolResult:
    if not sandbox.write_allowed:
        return ToolResult(ok=False, payload={"error": "write_file is disabled: sandbox-only access mode"})
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    reason = sandbox.check_write_path(path)
    if reason:
        return ToolResult(ok=False, payload={"error": reason})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.content, encoding="utf-8")
    return ToolResult(ok=True, payload={"path": args.path, "bytes": len(args.content.encode("utf-8"))})


def apply_patch(sandbox: WorkdirSandbox, args: ApplyPatchArgs) -> ToolResult:
    if not sandbox.write_allowed:
        return ToolResult(ok=False, payload={"error": "apply_patch is disabled: sandbox-only access mode"})
    try:
        path = sandbox.resolve(args.path)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return ToolResult(ok=False, payload={"error": str(exc)})
    reason = sandbox.check_write_path(path)
    if reason:
        return ToolResult(ok=False, payload={"error": reason})
    if not path.exists() or not path.is_file():
        return ToolResult(ok=False, payload={"error": f"File not found: {args.path}"})
    text = path.read_text(encoding="utf-8")
    count = text.count(args.old_text)
    if count != 1:
        return ToolResult(
            ok=False,
            payload={"error": f"old_text matched {count} times; expected 1", "path": args.path},
        )
    path.write_text(text.replace(args.old_text, args.new_text, 1), encoding="utf-8")
    return ToolResult(ok=True, payload={"path": args.path, "patched": True})


def write_summary(path: str, content: str) -> str:
    preview = content if len(content) <= 4000 else content[:4000] + "\n...truncated..."
    return f"write_file {path}\n{preview}"


def patch_diff(path: str, old: str, new: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n- {old}\n+ {new}"
