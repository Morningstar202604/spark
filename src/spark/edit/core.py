"""P1-2：Context editing 文件改写核心（aider SEARCH/REPLACE 开放格式）。

实现 aider edit-block 开放格式语义，**未引入 aider 整包**以保持轻量
（aider-chat dry-run 会拉入 ~80 个包：scipy / tree-sitter-language-pack 20MB /
tokenizers / tiktoken / huggingface_hub / sounddevice / pypandoc / mixpanel + posthog
遥测 / 独立 litellm+openai 客户端，违反反臃肿约束）。此处只实现其公开格式规范：

    <<<<<<< SEARCH
    <原文，必须在文件中唯一匹配>
    =======
    <替换文>
    >>>>>>> REPLACE

策略：
- **exact 匹配**：SEARCH 原文在当前文本中恰好出现 1 次 → 直接替换。
- **fuzzy 容错**：exact 为 0 次时，按行做空白容差匹配（先容尾随空白，再容首尾
  缩进漂移），恰好 1 处 → 容错替换。
- **唯一性强约束**：任何阶段匹配到 >1 处 → 冲突错误，**拒绝写入**（不猜、不静默
  取第一处）。
- **whole-file 自适应**：content 中没有 SEARCH 块标记时，视为整文件写入
  （小文件 / 模型直接给全文时总是 apply 成功）。
- **多 block 顺序应用**：blocks 按给出顺序逐个在上一步结果上应用（原子性：全部
  成功才产出新文本，任一块失败即抛错、不落盘）。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

SEARCH_MARK = "<<<<<<< SEARCH"
DIVIDER = "======="
REPLACE_MARK = ">>>>>>> REPLACE"


class EditFormatError(Exception):
    """SEARCH/REPLACE 格式非法（标记不配对）。"""


class EditConflictError(Exception):
    """SEARCH 匹配到多处（不唯一），拒绝写入。"""


class EditNoMatchError(Exception):
    """SEARCH 精确与模糊匹配都为 0 处，拒绝写入。"""


@dataclass(frozen=True)
class EditBlock:
    search: str
    replace: str


@dataclass(frozen=True)
class MatchInfo:
    start: int  # 字符偏移（在被匹配文本中）
    end: int
    mode: str  # "exact" | "ws" | "indent"


@dataclass(frozen=True)
class BlockApplyInfo:
    index: int
    mode: str
    search_preview: str = ""


@dataclass(frozen=True)
class ApplyResult:
    new_text: str
    mode: str  # "search_replace" | "whole_file"
    blocks: list[BlockApplyInfo] = field(default_factory=list)


def has_edit_blocks(content: str) -> bool:
    """content 是否包含 SEARCH/REPLACE 块标记。"""
    return any(line.startswith(SEARCH_MARK) for line in content.splitlines())


def parse_blocks(content: str) -> list[EditBlock]:
    """把模型输出解析为有序 EditBlock 列表。

    格式非法（SEARCH 后缺 ======= 或 REPLACE 收尾）抛 EditFormatError；
    没有任何块标记时返回空列表（交由调用方走 whole-file 分支）。
    """
    lines = content.split("\n")
    blocks: list[EditBlock] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.startswith(SEARCH_MARK):
            i += 1
            continue

        # SEARCH body
        i += 1
        search_body: list[str] = []
        while i < n and lines[i] != DIVIDER:
            search_body.append(lines[i])
            i += 1
        if i >= n:
            raise EditFormatError("SEARCH block missing '=======' divider")

        # REPLACE body (skip divider)
        i += 1
        replace_body: list[str] = []
        while i < n and not lines[i].startswith(REPLACE_MARK):
            replace_body.append(lines[i])
            i += 1
        if i >= n:
            raise EditFormatError("SEARCH block missing '>>>>>>> REPLACE' end marker")

        blocks.append(EditBlock(search=_trim_block(search_body), replace=_trim_block(replace_body)))

    return blocks


def _trim_block(body_lines: list[str]) -> str:
    """块体去掉首尾各一个空行（aider 惯例：标记行本身产生的边界空行）。"""
    if body_lines and body_lines[0].strip() == "":
        body_lines = body_lines[1:]
    if body_lines and body_lines[-1].strip() == "":
        body_lines = body_lines[:-1]
    return "\n".join(body_lines)


def _exact_spans(text: str, search: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    if not search:
        return spans
    start = 0
    while True:
        idx = text.find(search, start)
        if idx == -1:
            break
        spans.append((idx, idx + len(search)))
        start = idx + 1  # 允许重叠计数，保证多匹配被发现
    return spans


def _line_char_bounds(text: str) -> list[int]:
    """每行起始字符偏移（含行尾换行），len == lines + 1。"""
    bounds = [0]
    for ln in text.split("\n"):
        bounds.append(bounds[-1] + len(ln) + 1)
    return bounds


def _fuzzy_spans(
    text: str, search: str, key
) -> list[tuple[int, int]]:
    """按行做空白容差匹配；返回字符区间列表（恰好 0/1/>1 都可能）。"""
    file_lines = text.split("\n")
    search_lines = search.split("\n")
    m = len(search_lines)
    if m == 0 or m > len(file_lines):
        return []
    fkeys = [key(line) for line in file_lines]
    skeys = [key(line) for line in search_lines]
    bounds = _line_char_bounds(text)
    spans: list[tuple[int, int]] = []
    for i in range(0, len(file_lines) - m + 1):
        if fkeys[i : i + m] == skeys:
            start = bounds[i]
            end = bounds[i + m] - 1  # 去掉搜索块最后一行末尾的换行
            # 若 text 以换行结尾，bounds[last] = len+1，end 可能越界到末尾换行
            if end > len(text):
                end = len(text)
            spans.append((start, end))
    return spans


def locate(text: str, search: str) -> MatchInfo:
    """在 text 中定位 search；唯一才返回，否则抛冲突/无匹配。"""
    exact = _exact_spans(text, search)
    if len(exact) == 1:
        (s, e) = exact[0]
        return MatchInfo(s, e, "exact")
    if len(exact) > 1:
        raise EditConflictError(f"SEARCH matched {len(exact)} times (exact); expected exactly 1")

    # fuzzy level 1：容尾随空白
    ws = _fuzzy_spans(text, search, key=lambda s: s.rstrip())
    if len(ws) == 1:
        (s, e) = ws[0]
        return MatchInfo(s, e, "ws")
    if len(ws) > 1:
        raise EditConflictError(f"SEARCH matched {len(ws)} times (whitespace-tolerant); expected 1")

    # fuzzy level 2：容首尾缩进漂移
    ind = _fuzzy_spans(text, search, key=lambda s: s.strip())
    if len(ind) == 1:
        (s, e) = ind[0]
        return MatchInfo(s, e, "indent")
    if len(ind) > 1:
        raise EditConflictError(f"SEARCH matched {len(ind)} times (indent-tolerant); expected 1")

    raise EditNoMatchError("SEARCH not found (exact or fuzzy) in target file")


def apply_blocks(old_text: str, blocks: list[EditBlock]) -> ApplyResult:
    """顺序应用多个块；任一块失败抛错（调用方据此不落盘）。"""
    cur = old_text
    infos: list[BlockApplyInfo] = []
    for idx, blk in enumerate(blocks):
        hit = locate(cur, blk.search)
        cur = cur[: hit.start] + blk.replace + cur[hit.end :]
        infos.append(
            BlockApplyInfo(
                index=idx, mode=hit.mode, search_preview=blk.search.split("\n")[0][:60]
            )
        )
    return ApplyResult(new_text=cur, mode="search_replace", blocks=infos)


def compute_new_text(old_text: str, content: str) -> ApplyResult:
    """自适应入口：content 无块 → whole_file；有块 → apply_blocks。"""
    if not has_edit_blocks(content):
        return ApplyResult(new_text=content, mode="whole_file")
    blocks = parse_blocks(content)
    if not blocks:
        # 有标记行但解析不出块（理论上 parse 会抛错），退回整文件
        return ApplyResult(new_text=content, mode="whole_file")
    return apply_blocks(old_text, blocks)


MAX_DIFF_CHARS = 6000


def unified_diff(path_str: str, before: str, after: str) -> str:
    """产出与前端 DiffView.tsx 兼容的 unified diff 文本。"""
    lines = list(
        difflib.unified_diff(
            before.split("\n"),
            after.split("\n"),
            fromfile=f"a/{path_str}",
            tofile=f"b/{path_str}",
            lineterm="",
            n=3,
        )
    )
    if not lines:
        return f"{path_str}: no change"
    text = "\n".join(lines)
    if len(text) > MAX_DIFF_CHARS:
        return text[:MAX_DIFF_CHARS] + "\n...diff truncated..."
    return text
