"""apply_patch 工具：一次应用多文件 unified diff 补丁（走审批门）。

与 write_file 的分工（符合人的逻辑）：
- 改单个文件、内容大改 → write_file（预览单文件 diff）
- 同时改多个相关文件（重构、批量修改）→ apply_patch（一次给全量 diff，
  审批弹窗按文件分组展示，用户看完整套改动再决定允许/拒绝）

安全融合：
- 路径边界在解析阶段校验（工作目录之外/受保护路径 = 整体拒绝，不写任何文件）
- 工具类别为 write，审批档位与"会话内始终允许"语义与 write_file 完全一致
- 应用前自动 git 检查点（见 loop.py：写类工具统一处理）
"""
from __future__ import annotations

from spark2.patch_apply import PatchError, apply_patch, preview_patch
from spark2.tools.base import Tool, ToolContext

MAX_DIFF_SHOW = 60_000


def _preview(args: dict, ctx: ToolContext) -> tuple[str, str]:
    patch = str(args.get("patch", ""))
    try:
        files = preview_patch(patch, ctx.workdir, ctx.protected)
    except PatchError as e:
        return f"补丁无法应用：{e}", patch[:MAX_DIFF_SHOW]
    n = len(files)
    add = sum(f.added for f in files)
    rem = sum(f.removed for f in files)
    return f"应用补丁：{n} 个文件，+{add}/-{rem} 行", patch[:MAX_DIFF_SHOW]


async def _apply(args: dict, ctx: ToolContext) -> str:
    patch = str(args.get("patch", ""))
    try:
        result, _changes = apply_patch(patch, ctx.workdir, ctx.protected)
        return result
    except PatchError as e:
        return f"补丁应用失败：{e}"


def build_patch_tool() -> list[Tool]:
    return [
        Tool(
            name="apply_patch",
            description=(
                "应用统一 diff（unified diff）补丁，可一次修改多个文件（重构/批量修改时用）。"
                "patch：diff 文本，格式为 --- 文件头 + @@ hunk + 增删行；"
                "路径必须位于工作目录内，越界会被拒绝。改单个文件也可用 write_file。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "patch": {
                        "type": "string",
                        "description": "unified diff 文本（可含多个文件段）",
                    }
                },
                "required": ["patch"],
            },
            category="write",
            handler=_apply,
            preview=_preview,
        ),
    ]
