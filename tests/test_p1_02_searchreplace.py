"""P1-2：Context editing 文件改写策略测试。

覆盖：exact 匹配 / fuzzy 容错 / 多 block 顺序应用 / 无匹配报错 / 多匹配冲突 /
whole-file 回退 / diff 输出格式 / 与 repo_map tree-sitter AST 联动。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.sandbox import WorkdirSandbox
from spark.edit import (
    ApplyEditArgs,
    EditConflictError,
    EditFormatError,
    EditNoMatchError,
    apply_blocks,
    apply_edit,
    apply_edit_diff,
    ast_sanity_check,
    compute_new_text,
    has_edit_blocks,
    locate,
    parse_blocks,
    unified_diff,
)


def _block(search: str, replace: str) -> str:
    return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n"


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #

def test_parse_multi_blocks() -> None:
    content = _block("a = 1", "a = 2") + "\n" + _block("b = 2", "b = 3")
    blocks = parse_blocks(content)
    assert len(blocks) == 2
    assert blocks[0].search == "a = 1"
    assert blocks[0].replace == "a = 2"
    assert blocks[1].search == "b = 2"


def test_parse_missing_divider_raises() -> None:
    with pytest.raises(EditFormatError):
        parse_blocks("<<<<<<< SEARCH\nfoo\n>>>>>>> REPLACE\n")


def test_parse_missing_end_marker_raises() -> None:
    with pytest.raises(EditFormatError):
        parse_blocks("<<<<<<< SEARCH\nfoo\n=======\nbar\n")


def test_has_edit_blocks() -> None:
    assert has_edit_blocks(_block("x", "y"))
    assert not has_edit_blocks("just a whole file\ncontent\n")


# --------------------------------------------------------------------------- #
# 匹配：exact / fuzzy
# --------------------------------------------------------------------------- #

def test_locate_exact() -> None:
    text = "head\nold line\ntail\n"
    hit = locate(text, "old line")
    assert hit.mode == "exact"
    assert text[hit.start : hit.end] == "old line"


def test_locate_fuzzy_trailing_whitespace() -> None:
    # 文件里行尾多了空白，模型 SEARCH 不带 → 走 ws 容错
    text = "def f():\n    return 1   \n"
    hit = locate(text, "def f():\n    return 1")
    assert hit.mode in {"exact", "ws"}
    assert "return 1" in text[hit.start : hit.end]


def test_locate_fuzzy_indent_drift() -> None:
    # 模型多发了 4 空格缩进 → 走 indent 容错
    text = "class A:\n    pass\n\ntarget = 1\n"
    hit = locate(text, "    target = 1")
    assert hit.mode == "indent"
    assert "target = 1" in text[hit.start : hit.end]


def test_locate_no_match_raises() -> None:
    with pytest.raises(EditNoMatchError):
        locate("alpha\nbeta\n", "gamma")


def test_locate_multiple_match_conflict() -> None:
    text = "x = 1\nx = 1\n"
    with pytest.raises(EditConflictError):
        locate(text, "x = 1")


# --------------------------------------------------------------------------- #
# 多 block 顺序应用 + 原子性
# --------------------------------------------------------------------------- #

def test_apply_blocks_sequential() -> None:
    old = "v1 = 1\nv2 = 2\n"
    blocks = parse_blocks(
        _block("v1 = 1", "v1 = 100") + "\n" + _block("v2 = 2", "v2 = 200")
    )
    res = apply_blocks(old, blocks)
    assert res.new_text == "v1 = 100\nv2 = 200\n"
    assert [b.mode for b in res.blocks] == ["exact", "exact"]


def test_apply_blocks_atomic_on_failure() -> None:
    """第二个块失败时第一个块的改动也不得落盘（调用方据此不写）。"""
    old = "a = 1\nb = 2\n"
    blocks = parse_blocks(_block("a = 1", "a = 9") + "\n" + _block("missing", "x"))
    with pytest.raises(EditNoMatchError):
        apply_blocks(old, blocks)


# --------------------------------------------------------------------------- #
# whole-file 自适应回退
# --------------------------------------------------------------------------- #

def test_compute_whole_file_when_no_blocks() -> None:
    old = "old\n"
    res = compute_new_text(old, brand_new := "brand\nnew\n")
    assert res.mode == "whole_file"
    assert res.new_text == "brand\nnew\n"


def test_compute_search_replace_mode() -> None:
    res = compute_new_text("a = 1\n", _block("a = 1", "a = 2"))
    assert res.mode == "search_replace"
    assert res.new_text == "a = 2\n"


# --------------------------------------------------------------------------- #
# diff 输出格式（与前端 DiffView.tsx 兼容）
# --------------------------------------------------------------------------- #

def test_unified_diff_format() -> None:
    diff = unified_diff("pkg/mod.py", "a = 1\n", "a = 2\n")
    assert diff.startswith("--- a/pkg/mod.py")
    assert "+++ b/pkg/mod.py" in diff
    assert "@@" in diff
    assert "-a = 1" in diff
    assert "+a = 2" in diff


def test_unified_diff_no_change() -> None:
    assert "no change" in unified_diff("x.py", "same\n", "same\n")


# --------------------------------------------------------------------------- #
# 与 repo_map tree-sitter AST 联动
# --------------------------------------------------------------------------- #

def test_ast_gate_allows_valid_python() -> None:
    r = ast_sanity_check(".py", "def f():\n    return 1\n", "def f():\n    return 2\n")
    assert r.skipped is False
    assert r.syntax_ok is True


def test_ast_gate_rejects_syntax_error() -> None:
    # 缺冒号 → tree-sitter 报语法错误
    r = ast_sanity_check(".py", "def f():\n    return 1\n", "def f()\n    return 2\n")
    assert r.syntax_ok is False
    assert "line" in r.error


def test_ast_symbol_removal_warning() -> None:
    old = "def keep():\n    return 1\n\ndef gone():\n    return 2\n"
    new = "def keep():\n    return 9\n"
    r = ast_sanity_check(".py", old, new)
    assert r.syntax_ok is True
    assert "gone" in r.removed_symbols


def test_ast_skips_non_code() -> None:
    r = ast_sanity_check(".md", "# hi\n", "# hi\n")
    assert r.skipped is True


# --------------------------------------------------------------------------- #
# 工具层（sandbox 桥接 + 落盘 + 审批 diff）
# --------------------------------------------------------------------------- #

def _sandbox(tmp_path: Path) -> WorkdirSandbox:
    return WorkdirSandbox(tmp_path)


def test_apply_edit_tool_search_replace(tmp_path: Path) -> None:
    box = _sandbox(tmp_path)
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    args = ApplyEditArgs(path="mod.py", content=_block("return 1", "return 42"))
    result = apply_edit(box, args)
    assert result.ok, result.payload
    assert result.payload["mode"] == "search_replace"
    assert result.payload["blocks_applied"] == 1
    assert "return 42" in (tmp_path / "mod.py").read_text()
    # diff 已产出且可被 DiffView 解析
    assert "+++ b/mod.py" in result.payload["diff"]


def test_apply_edit_tool_whole_file_creates_new(tmp_path: Path) -> None:
    box = _sandbox(tmp_path)
    result = apply_edit(box, ApplyEditArgs(path="new.txt", content="hello\n"))
    assert result.ok, result.payload
    assert result.payload["mode"] == "whole_file"
    assert (tmp_path / "new.txt").read_text() == "hello\n"


def test_apply_edit_tool_refuses_on_no_match(tmp_path: Path) -> None:
    box = _sandbox(tmp_path)
    (tmp_path / "mod.py").write_text("a = 1\n", encoding="utf-8")
    result = apply_edit(box, ApplyEditArgs(path="mod.py", content=_block("zzz", "q")))
    assert not result.ok
    assert "not found" in result.payload["error"]
    # 文件未被改动
    assert (tmp_path / "mod.py").read_text() == "a = 1\n"


def test_apply_edit_tool_refuses_on_conflict(tmp_path: Path) -> None:
    box = _sandbox(tmp_path)
    (tmp_path / "mod.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    result = apply_edit(box, ApplyEditArgs(path="mod.py", content=_block("x = 1", "x = 2")))
    assert not result.ok
    assert "times" in result.payload["error"]
    assert (tmp_path / "mod.py").read_text() == "x = 1\nx = 1\n"


def test_apply_edit_tool_refuses_syntax_error(tmp_path: Path) -> None:
    box = _sandbox(tmp_path)
    (tmp_path / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    # 替换后缺冒号 → AST 门禁拒绝
    content = _block("def f():\n    return 1", "def f()\n    return 2")
    result = apply_edit(box, ApplyEditArgs(path="mod.py", content=content))
    assert not result.ok
    assert "syntax error" in result.payload["error"]
    assert (tmp_path / "mod.py").read_text() == "def f():\n    return 1\n"


def test_apply_edit_diff_preview_no_write(tmp_path: Path) -> None:
    box = _sandbox(tmp_path)
    (tmp_path / "mod.py").write_text("a = 1\n", encoding="utf-8")
    diff = apply_edit_diff(box, "mod.py", _block("a = 1", "a = 2"))
    assert "+++ b/mod.py" in diff
    # 预演不落盘
    assert (tmp_path / "mod.py").read_text() == "a = 1\n"


def test_apply_edit_diff_preview_refusal_text(tmp_path: Path) -> None:
    box = _sandbox(tmp_path)
    (tmp_path / "mod.py").write_text("a = 1\n", encoding="utf-8")
    diff = apply_edit_diff(box, "mod.py", _block("nope", "x"))
    assert "refused" in diff or "not found" in diff
