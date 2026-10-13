"""P1-2：AST 感知 sanity check（复用 repo_map 的 tree-sitter，不新增依赖）。

对改写后的文本做语法级 sanity check：
- 受支持语言（.py/.ts/.tsx/.js/.jsx）用 tree-sitter 解析；
- 新文本 `root_node.has_error` 为真 → 改写引入了语法错误，拒绝落盘；
- 顺带对比新旧符号集（函数/类），把「被删掉的顶层符号」作为警告返回，
  帮助模型/人发现误删。
- 无 grammar / 非代码文件 → 跳过（不阻塞，降级为纯文本改写）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from spark.repo_map import _EXT_LANG, _symbol_query, _try_language

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AstCheckResult:
    skipped: bool = False          # 非代码/无 grammar → 不做 AST 门禁
    syntax_ok: bool = True         # 新文本是否无语法错误（skipped 时恒 True）
    error: str = ""                # 语法错误描述
    removed_symbols: list[str] = field(default_factory=list)
    added_symbols: list[str] = field(default_factory=list)


def _symbols_from_text(lang_name: str, text: str) -> list[str]:
    """在内存文本上提取符号名（repo_map._extract_symbols 是按文件读的，这里按文本）。"""
    lang = _try_language(lang_name)
    if lang is None:
        return []
    patterns = _symbol_query(lang_name)
    if not patterns:
        return []
    try:
        from tree_sitter import Parser, Query, QueryCursor

        source = text.encode("utf-8", "replace")
        tree = Parser(lang).parse(source)
        out: list[str] = []
        for pattern in patterns:
            try:
                query = Query(lang, pattern)
            except Exception:  # pragma: no cover - grammar 版本差异
                continue
            try:
                captures = QueryCursor(query).captures(tree.root_node)
            except Exception:  # pragma: no cover - 旧 API
                continue
            if captures is None:
                continue
            nodes = (
                [nd for group in captures.values() for nd in group]
                if isinstance(captures, dict)
                else [item[0] for item in captures]
            )
            for nd in nodes:
                name = source[nd.start_byte : nd.end_byte].decode("utf-8", "replace").strip()
                if name and name not in out:
                    out.append(name)
        return out
    except Exception as exc:  # pragma: no cover - 环境差异
        logger.debug("ast symbol extract failed: %s", exc)
        return []


def _has_syntax_error(lang_name: str, text: str) -> str:
    """返回语法错误描述；无错误返回空串。失败视为无错误（不阻塞）。"""
    lang = _try_language(lang_name)
    if lang is None:
        return ""
    try:
        from tree_sitter import Parser

        tree = Parser(lang).parse(text.encode("utf-8", "replace"))
        root = tree.root_node
        if not root.has_error:
            return ""
        # 找到第一个 error 节点给出行号
        stack = [root]
        while stack:
            node = stack.pop()
            if node.is_error or node.type == "ERROR":
                return f"syntax error near line {node.start_point[0] + 1}"
            stack.extend(node.children)
        return "syntax error detected"
    except Exception as exc:  # pragma: no cover
        logger.debug("ast parse failed: %s", exc)
        return ""


def ast_sanity_check(suffix: str, old_text: str, new_text: str) -> AstCheckResult:
    """对改动后的新文本做门禁；返回结果（skipped 时调用方不应拦截）。"""
    lang = _EXT_LANG.get(suffix)
    if not lang:
        return AstCheckResult(skipped=True)

    err = _has_syntax_error(lang, new_text)
    if err:
        return AstCheckResult(syntax_ok=False, error=err)

    old_syms = set(_symbols_from_text(lang, old_text))
    new_syms = set(_symbols_from_text(lang, new_text))
    removed = sorted(old_syms - new_syms)
    added = sorted(new_syms - old_syms)
    return AstCheckResult(syntax_ok=True, removed_symbols=removed, added_symbols=added)
