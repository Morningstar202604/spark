"""会话导出工具：把会话导出为 Markdown / JSON。

对标 Claude Code session export / Cursor session share。
模型通过 ctx 无法拿到 session_id，因此该工具以 sid 为参数（缺省时由上层
server 在调用前注入），导出文件落在 workdir/spark2_exports/。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from spark2.store import SessionStore
from spark2.tools.base import Tool, ToolContext


def _msg_to_md(msg: dict) -> str:
    role = msg.get("role", "system")
    content = msg.get("content", "")
    if isinstance(content, list):
        content = "\n".join(
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    content = str(content).strip()
    if not content:
        return ""
    label = {"user": "你", "assistant": "Spark", "tool": "工具"}.get(role, role)
    lines = [f"## {label}", "", content, ""]
    return "\n".join(lines)


def export_markdown(sid: str, workdir: Path) -> str:
    """导出会话为 Markdown 文件，返回文件路径。"""
    store = SessionStore()
    meta = store.meta(sid)
    if meta is None:
        return f"错误：会话 {sid} 不存在"
    msgs = store.messages(sid)
    lines = [f"# {meta.get('title', '会话')}", ""]
    lines.append(f"- 工作目录：{meta.get('workdir', '')}")
    lines.append(f"- 模型：{meta.get('model', '')}")
    lines.append(f"- 创建：{meta.get('created', '')}")
    lines.append(f"- 导出：{datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- 消息数：{len(msgs)}")
    lines.append("")
    for m in msgs:
        md = _msg_to_md(m)
        if md:
            lines.append(md)
    out_dir = workdir / "spark2_exports"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(
        c for c in str(meta.get("title", "session"))[:30] if c.isalnum() or c in (" ", "_", "-")
    ).replace(" ", "_")
    path = out_dir / f"session_{ts}_{safe_title}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


async def _export_session(args: dict, ctx: ToolContext) -> str:
    sid = str(args.get("sid") or "").strip()
    if not sid:
        return "错误：缺少 sid（会话 id）。请向用户询问要导出的会话 id，或使用 /export <会话id>。"
    fmt = str(args.get("format") or "markdown").lower()
    if fmt == "json":
        store = SessionStore()
        meta = store.meta(sid)
        if meta is None:
            return f"错误：会话 {sid} 不存在"
        msgs = store.messages(sid)
        out = {"meta": meta, "messages": msgs}
        out_dir = ctx.workdir / "spark2_exports"
        out_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"session_{ts}_{sid}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return f"已导出 JSON：{path}"
    path = export_markdown(sid, ctx.workdir)
    return f"已导出 Markdown：{path}"


def build_session_tools() -> list[Tool]:
    return [
        Tool(
            name="export_session",
            description=(
                "把当前会话导出为 Markdown 或 JSON 文件（保存到 workdir/spark2_exports/）。"
                "sid：会话 id（可选，缺省为当前会话）；format：markdown|json。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "sid": {"type": "string", "description": "会话 id，缺省为当前"},
                    "format": {"type": "string", "description": "导出格式：markdown 或 json"},
                },
            },
            category="system",
            handler=_export_session,
            preview=lambda a, c: (f"导出会话 {a.get('sid') or '当前'}", ""),
        ),
    ]
