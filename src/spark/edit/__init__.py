"""P1-2 Context editing：SEARCH/REPLACE + whole-file 自适应改写。

公开 API：
- ``parse_blocks`` / ``has_edit_blocks``：解析模型输出中的编辑块。
- ``apply_blocks`` / ``compute_new_text``：纯文本改写（exact + fuzzy + 唯一性强约束）。
- ``unified_diff``：产出与前端 DiffView 兼容的 diff。
- ``ast_sanity_check``：tree-sitter 语法门禁（复用 repo_map，无新增依赖）。
- ``apply_edit`` / ``apply_edit_diff``：注册进 ToolRegistry 的工具实现。
"""

from __future__ import annotations

from spark.edit.ast_check import AstCheckResult, ast_sanity_check
from spark.edit.core import (
    ApplyResult,
    EditBlock,
    EditConflictError,
    EditFormatError,
    EditNoMatchError,
    MatchInfo,
    apply_blocks,
    compute_new_text,
    has_edit_blocks,
    locate,
    parse_blocks,
    unified_diff,
)
from spark.edit.tool import ApplyEditArgs, apply_edit, apply_edit_diff

__all__ = [
    "ApplyEditArgs",
    "ApplyResult",
    "AstCheckResult",
    "EditBlock",
    "EditConflictError",
    "EditFormatError",
    "EditNoMatchError",
    "MatchInfo",
    "apply_blocks",
    "apply_edit",
    "apply_edit_diff",
    "ast_sanity_check",
    "compute_new_text",
    "has_edit_blocks",
    "locate",
    "parse_blocks",
    "unified_diff",
]
