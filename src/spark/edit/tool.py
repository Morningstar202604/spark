"""P1-2：apply_edit 工具——SEARCH/REPLACE 块改写 + whole-file 自适应 + AST 门禁。

接入方式（二选一结论）：**新增独立工具 ``apply_edit``**，而非挂到 ``fs.write_file``。
理由：
- ``write_file`` 的既有语义是「整文件替换」，模型已依赖该契约；若在其中静默解析
  SEARCH/REPLACE 块，会改变其行为、把一次「整文件写」误判成补丁，难以排查。
- 独立工具让模型显式选择改写方式，并把「块解析 + 唯一性强约束 + AST 门禁 +
  结构化 diff」收敛在一处；对既有 fs 工具零侵入（追加式注册）。
- 与 P0-8 DiffView 联动：本工具产出 unified diff 文本（``a/path b/path`` + hunks），
  正是前端 DiffView.tsx 解析的数据形态，审批卡片与结果流可直接渲染。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from spark.errors import PathEscapeError
from spark.models import ToolResult
from spark.sandbox import SandboxPolicyError, WorkdirSandbox

from spark.edit.ast_check import ast_sanity_check
from spark.edit.core import (
    EditConflictError,
    EditFormatError,
    EditNoMatchError,
    apply_blocks,
    compute_new_text,
    has_edit_blocks,
    parse_blocks,
    unified_diff,
)


def _fs_helpers():
    """惰性取 fs 解码/编码助手，避免 edit.tool ↔ tools.registry 循环导入。"""
    from spark.tools.fs import _decode_file, _encode_file

    return _decode_file, _encode_file


class ApplyEditArgs(BaseModel):
    path: str
    content: str


def _read_old(sandbox: WorkdirSandbox, path: Path) -> tuple[str, str, str]:
    """返回 (old_text, newline, bom)；文件不存在时 old_text=""。"""
    _decode_file, _ = _fs_helpers()
    if path.is_file():
        return _decode_file(path)
    return "", "\n", ""


def _fail(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=False, payload=payload)


def _plan_edit(sandbox: WorkdirSandbox, path_str: str, content: str) -> ToolResult | tuple[str, str, str, str, list]:
    """预演改写（不写盘），供工具与审批 diff 复用。

    返回 (old_text, new_text, newline, bom, apply_infos) 或 ToolResult(失败)。
    """
    try:
        path = sandbox.resolve(path_str)
    except (PathEscapeError, SandboxPolicyError) as exc:
        return _fail({"error": str(exc)})
    reason = sandbox.check_write_path(path)
    if reason:
        return _fail({"error": reason})

    old_text, newline, bom = _read_old(sandbox, path)

    try:
        result = compute_new_text(old_text, content)
    except (EditFormatError, EditNoMatchError, EditConflictError) as exc:
        return _fail(
            {
                "error": str(exc),
                "path": path_str,
                "hint": "SEARCH block must match exactly one location; re-read the file and re-send a unique SEARCH.",
            }
        )

    # AST 门禁：仅对支持的代码语言生效；语法错误拒绝落盘
    ast = ast_sanity_check(path.suffix, old_text, result.new_text)
    if not ast.syntax_ok:
        return _fail(
            {
                "error": f"refused: edit would introduce a syntax error ({ast.error})",
                "path": path_str,
            }
        )

    return old_text, result.new_text, newline, bom, result.blocks


def apply_edit(sandbox: WorkdirSandbox, args: ApplyEditArgs) -> ToolResult:
    if not sandbox.write_allowed:
        return _fail({"error": "apply_edit is disabled: sandbox-only access mode"})

    planned = _plan_edit(sandbox, args.path, args.content)
    if isinstance(planned, ToolResult):
        return planned
    old_text, new_text, newline, bom, infos = planned

    path = sandbox.resolve(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _decode_file, _encode_file = _fs_helpers()
    try:
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(_encode_file(new_text, newline, bom))
    except OSError as exc:
        return _fail({"error": f"cannot write file: {exc}", "path": args.path})

    mode = "search_replace" if has_edit_blocks(args.content) else "whole_file"
    ast = ast_sanity_check(path.suffix, old_text, new_text)
    payload: dict[str, Any] = {
        "path": args.path,
        "mode": mode,
        "blocks_applied": len(infos),
        "diff": unified_diff(args.path, old_text, new_text),
    }
    if infos:
        payload["block_modes"] = [{"index": b.index, "mode": b.mode} for b in infos]
    if ast.removed_symbols:
        payload["ast_removed_symbols"] = ast.removed_symbols
    if ast.added_symbols:
        payload["ast_added_symbols"] = ast.added_symbols
    return ToolResult(ok=True, payload=payload)


def apply_edit_diff(sandbox: WorkdirSandbox, path: str, content: str) -> str:
    """审批预览：不落盘，返回 unified diff 文本（失败时返回原因文本）。"""
    planned = _plan_edit(sandbox, path, content)
    if isinstance(planned, ToolResult):
        return f"apply_edit {path}: {planned.payload.get('error', 'refused')}"
    old_text, new_text, _nl, _bom, _infos = planned
    return unified_diff(path, old_text, new_text)


__all__ = [
    "ApplyEditArgs",
    "apply_edit",
    "apply_edit_diff",
    "parse_blocks",
    "has_edit_blocks",
]
