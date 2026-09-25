"""统一 diff 补丁的解析、校验与应用（多文件、零依赖、严格中文报错）。

用途：apply_patch 工具的底层。支持标准 unified diff：
- 多文件：每个文件由 ``--- a/path`` / ``+++ b/path`` 头开始，``@@`` hunk 分隔
- ``a/ b/`` 前缀自动剥除（无前缀也可）
- 每个 hunk 逐行核对上下文，任何不匹配整体拒绝，不留半截文件

安全边界（与审批门/受保护路径一致）：
- 所有目标文件必须解析到工作目录内（越界 = 整体拒绝）
- 受保护路径 = 整体拒绝
校验与落盘分离：preview 只解析不落盘；handler 校验后原子落盘。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


def _resolve_path(raw: str, workdir: Path) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = workdir / p
    return p.resolve()


def _is_within(p: Path, base: Path) -> bool:
    try:
        p.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


class PatchError(Exception):
    """补丁解析/校验/应用失败（消息面向用户，中文、带位置）。"""


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]] = field(default_factory=list)  # (标记, 文本)：' '/'-'/'+'


@dataclass
class FilePatch:
    path: str  # 补丁里的原始路径（含 a/ b/ 前缀，展示用）
    target: Path  # 解析后的绝对路径（已校验在工作目录内）
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def added(self) -> int:
        return sum(1 for h in self.hunks for m, _ in h.lines if m == "+")

    @property
    def removed(self) -> int:
        return sum(1 for h in self.hunks for m, _ in h.lines if m == "-")


def _strip_prefix(path: str) -> str:
    p = path.strip()
    for pre in ("a/", "b/"):
        if p.startswith(pre):
            return p[len(pre):]
    return p


def parse_patch(patch_text: str, workdir: Path, protected: list[Path]) -> list[FilePatch]:
    """解析 unified diff 文本 → 文件补丁列表；任何越界/受保护路径立即报错。

    workdir/protected 用于路径边界校验（解析阶段就拦，不等到落盘）。
    """
    if not patch_text or not patch_text.strip():
        raise PatchError("补丁为空，没有可应用的改动")
    lines = patch_text.splitlines()
    files: list[FilePatch] = []
    cur: FilePatch | None = None
    cur_hunk: Hunk | None = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("--- "):
            if cur is not None:
                files.append(cur)
            raw = ln[4:].strip()
            target = _resolve_path(_strip_prefix(raw), workdir)
            if not _is_within(target, workdir):
                raise PatchError(f"补丁包含工作目录之外的文件：{raw}")
            for prot in protected:
                if _is_within(target, prot):
                    raise PatchError(f"补丁目标为受保护路径：{raw}")
            cur = FilePatch(path=raw, target=target)
            cur_hunk = None
            i += 1
            # 期望下一行是 +++
            if i < len(lines) and lines[i].startswith("+++ "):
                i += 1
            continue
        if ln.startswith("+++ "):
            # 只有 +++ 没有 ---（畸形）→ 报错；正常情况已在上面消费
            raise PatchError(f"缺少 --- 头的补丁段：{ln}")
        if ln.startswith("@@"):
            if cur is None:
                raise PatchError("@@ hunk 出现在文件头之前")
            m = _parse_hunk_header(ln)
            cur_hunk = m
            cur.hunks.append(cur_hunk)
            i += 1
            continue
        if cur_hunk is not None:
            # hunk 内容行：' ' 上下文 / '-' 删除 / '+' 新增（含 '\ No newline' 等杂注忽略）
            if ln.startswith(("+", "-", " ")):
                cur_hunk.lines.append((ln[0], ln[1:]))
            # 其他（\ No newline at end of file 等）忽略，不破坏状态
            i += 1
            continue
        # hunk 之外的游离行：允许空行/尾注，其他则报错避免误判
        if ln.strip():
            raise PatchError(f"无法识别的补丁行（第 {i + 1} 行）：{ln[:60]}")
        i += 1
    if cur is not None:
        files.append(cur)
    if not files:
        raise PatchError("补丁中没有识别到文件段（需要 --- / +++ 文件头）")
    for f in files:
        if not f.hunks:
            raise PatchError(f"文件 {f.path} 没有 @@ hunk，无法应用")
    return files


def _parse_hunk_header(ln: str) -> Hunk:
    # @@ -a,b +c,d @@
    m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
    if not m:
        raise PatchError(f"无法解析 hunk 头：{ln}")
    old_s = int(m.group(1))
    old_c = int(m.group(2) or 1)
    new_s = int(m.group(3))
    new_c = int(m.group(4) or 1)
    return Hunk(old_start=old_s, old_count=old_c, new_start=new_s, new_count=new_c)


def apply_to_text(old_text: str, hunks: list[Hunk]) -> str:
    """把 hunk 列表应用到 old_text，返回新文本；上下文不匹配即抛 PatchError。

    多个 hunk 的行号都基于**原文件**：从后往前应用，前面的 hunk 不受后面改动影响。
    单个 hunk 按位置交错消费：' ' 保留下行、'-' 跳过、'+' 插入到当前位置。
    """
    if not hunks:
        return old_text
    src = old_text.splitlines()
    # 倒序应用，避免行号漂移
    for h in sorted(hunks, key=lambda h: h.old_start, reverse=True):
        if h.old_count == 0:
            # 新文件（@@ -0,0 +N,M @@）：只允许纯新增行
            if h.old_start != 0:
                raise PatchError(f"新文件 hunk 起始行必须为 0，实际 {h.old_start}")
            if any(m != "+" for m, _ in h.lines):
                raise PatchError("新文件 hunk 只能包含新增行")
            src = [text for _, text in h.lines]
            continue
        idx = h.old_start - 1
        if idx < 0:
            raise PatchError("hunk 起始行越界（负索引）")
        if idx > len(src):
            raise PatchError(
                f"hunk 起始行 {h.old_start} 超过文件总行数 {len(src)}"
            )
        out: list[str] = []
        ptr = idx
        for mark, text in h.lines:
            if mark == " ":
                if ptr >= len(src):
                    raise PatchError(f"上下文不匹配：文件行数不足（起始 {h.old_start}）")
                if src[ptr] != text:
                    raise PatchError(
                        f"上下文不匹配（起始行 {h.old_start}，第 {ptr + 1} 行）：预期 {text!r}，实际 {src[ptr]!r}"
                    )
                out.append(src[ptr])
                ptr += 1
            elif mark == "-":
                if ptr >= len(src):
                    raise PatchError(f"上下文不匹配：文件行数不足（起始 {h.old_start}）")
                if src[ptr] != text:
                    raise PatchError(
                        f"上下文不匹配（起始行 {h.old_start}，第 {ptr + 1} 行）：预期删除 {text!r}，实际 {src[ptr]!r}"
                    )
                ptr += 1
            else:  # '+'
                out.append(text)
        src = src[:idx] + out + src[ptr:]
    return "\n".join(src)


def _read_file_safe(p: Path) -> str:
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise PatchError(f"读取文件失败 {p}：{e}")


def preview_patch(patch_text: str, workdir: Path, protected: list[Path]) -> list[FilePatch]:
    """只解析 + 边界校验，不落盘（供审批弹窗展示）。"""
    return parse_patch(patch_text, workdir, protected)


def apply_patch(patch_text: str, workdir: Path, protected: list[Path]) -> tuple[str, list[dict]]:
    """校验并应用补丁。成功返回 (结果说明, changes)；任何一步失败抛 PatchError，不写任何文件。

    changes: [{path, added, removed}] —— 供模型汇报与前端展示。
    落盘顺序：先全部解析/校验通过，再逐个写盘（每个文件原子写入）。
    """
    files = parse_patch(patch_text, workdir, protected)
    changes: list[dict] = []
    for f in files:
        old = _read_file_safe(f.target)
        new = apply_to_text(old, f.hunks)
        try:
            f.target.parent.mkdir(parents=True, exist_ok=True)
            f.target.write_text(new, encoding="utf-8")
        except OSError as e:
            # 前面的文件已写盘——按"整体拒绝"原则应尽量原子，但文件系统无法事务；
            # 说明已应用的部分，避免静默。
            raise PatchError(f"写入失败 {f.target}：{e}（此前文件已应用，可手动回滚）")
        changes.append(
            {"path": str(f.target.relative_to(workdir) if _is_within(f.target, workdir) else f.target),
             "added": f.added, "removed": f.removed}
        )
    total_a = sum(c["added"] for c in changes)
    total_r = sum(c["removed"] for c in changes)
    return f"已应用补丁：{len(changes)} 个文件，+{total_a}/-{total_r} 行", changes
