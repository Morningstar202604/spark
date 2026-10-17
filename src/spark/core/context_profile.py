"""P2：上下文火焰图——把"这轮请求里上下文都被谁吃了"变成可量化的剖面。

数据源：会话消息（store.load_messages）+ 与 AgentLoop 相同的估算口径
（estimate_message_tokens），输出：
- 总量 / 上限 / 占比
- 分段明细（system / agents.md / 每条消息，含角色与消息 id）
- 按角色聚合
- 浪费信号：同一文件被重复读取（read_file >2 次）、单条消息占比过大

供 CLI ``spark context-profile`` 与未来 Web 面板复用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from spark.config import SparkConfig
from spark.core.context import history_token_usage, load_agents_md, load_system_prompt
from spark.core.tokens import estimate_message_tokens
from spark.models import ChatMessage
from spark.store import SessionStore

BAR_WIDTH = 40


@dataclass
class Segment:
    kind: str
    label: str
    tokens: int
    message_id: int | None = None

    @property
    def bar(self) -> str:
        pct = self.percent
        filled = round(pct / 100 * BAR_WIDTH)
        return "█" * filled + "·" * (BAR_WIDTH - filled)

    percent: float = 0.0


@dataclass
class ContextProfile:
    total: int
    limit: int
    segments: list[Segment] = field(default_factory=list)
    waste_signals: list[str] = field(default_factory=list)

    def render(self, top: int = 25) -> str:
        lines = [
            f"上下文剖面：{self.total} / {self.limit} tokens "
            f"({round(self.total * 100 / max(1, self.limit), 1)}%)",
            "",
        ]
        by_kind: dict[str, int] = {}
        for seg in self.segments:
            by_kind[seg.kind] = by_kind.get(seg.kind, 0) + seg.tokens
        lines.append("— 按类别 —")
        for kind, tok in sorted(by_kind.items(), key=lambda kv: -kv[1]):
            pct = round(tok * 100 / max(1, self.total), 1)
            filled = round(pct / 100 * BAR_WIDTH)
            lines.append(f"{kind:<12}{'█' * filled}{'·' * (BAR_WIDTH - filled)} {pct:>5}% ({tok})")
        lines.append("")
        lines.append("— 明细（前 {} 大）—".format(top))
        for seg in sorted(self.segments, key=lambda s: -s.tokens)[:top]:
            label = seg.label[:60]
            lines.append(
                f"{seg.bar} {seg.percent:>5}%  {seg.kind:<10} {label}"
                + (f"  [#{seg.message_id}]" if seg.message_id else "")
            )
        if self.waste_signals:
            lines.append("")
            lines.append("— 浪费信号 —")
            lines += [f"⚠ {sig}" for sig in self.waste_signals]
        return "\n".join(lines)


def profile_context(
    store: SessionStore,
    session_id: str,
    workdir: Path,
    cfg: SparkConfig,
) -> ContextProfile:
    """对指定会话构建上下文剖面（与 AgentLoop 组装口径一致）。"""
    messages = store.load_messages(session_id)
    system_tokens = estimate_message_tokens(
        ChatMessage(role="system", content=load_system_prompt())
    )
    agents_md = load_agents_md(workdir, cfg)
    agents_tokens = (
        estimate_message_tokens(ChatMessage(role="user", content=agents_md))
        if agents_md
        else 0
    )

    segments: list[Segment] = []
    if system_tokens:
        segments.append(Segment("system", "system prompt", system_tokens))
    if agents_tokens:
        segments.append(Segment("agents", "AGENTS.md / CLAUDE.md", agents_tokens))

    waste: list[str] = []
    read_paths: dict[str, int] = {}
    for m in messages:
        tok = estimate_message_tokens(m)
        if m.role == "assistant" and m.tool_calls:
            for call in m.tool_calls:
                if call.name == "read_file":
                    try:
                        path = str(json.loads(str(call.arguments)).get("path", ""))
                    except (json.JSONDecodeError, TypeError):
                        path = "?"
                    read_paths[path] = read_paths.get(path, 0) + 1
            label = "assistant: " + " + ".join(
                f"{c.name}" for c in m.tool_calls
            )
            segments.append(Segment("assistant", label, tok, getattr(m, "id", None)))
        else:
            label = f"{m.role}: {(m.content or '')[:56].replace(chr(10), ' ')}"
            segments.append(Segment(m.role, label, tok, getattr(m, "id", None)))
        if tok > cfg.context.max_context_tokens * 0.25:
            mid = getattr(m, "id", None) or "?"
            waste.append(f"单条消息 #{mid} 占上限 {round(tok * 100 / max(1, cfg.context.max_context_tokens))}%（考虑拆分/精简）")

    for path, count in read_paths.items():
        if count > 2:
            waste.append(
                f"read_file 对 {path} 重复 {count} 次——结果可能被裁剪后模型在反复重读"
            )

    usage = history_token_usage(
        cfg=cfg, history=messages, workdir=workdir, tool_overhead_tokens=0
    )
    total = usage["used"]
    limit = usage["limit"] or cfg.context.max_context_tokens
    for seg in segments:
        seg.percent = round(seg.tokens * 100 / max(1, total), 1)
    return ContextProfile(total=total, limit=limit, segments=segments, waste_signals=waste)
