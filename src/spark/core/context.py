from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from spark.config import SparkConfig
from spark.core.tokens import estimate_message_tokens
from spark.models import ChatMessage


def load_system_prompt() -> str:
    return files("spark.prompts").joinpath("system.md").read_text(encoding="utf-8")


def load_agents_md(workdir: Path, cfg: SparkConfig) -> str | None:
    path = workdir / cfg.context.agents_md
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    limit = cfg.context.max_fragment_chars
    if len(text) > limit:
        return text[:limit]
    return text


MODE_HINTS = {
    "sandbox-only": "Access mode: sandbox-only. You can read files inside the workdir and plan, but shell execution and file writes are disabled. Focus on analysis, exploration and recommendations.",
    "workspace": "Access mode: workspace. You may read/write files and run shell commands inside the workdir only. Operations escaping the workdir or touching protected paths (including the Spark agent's own code under /workspace/src/spark) are blocked.",
    "full-access": "Access mode: full-access. You may operate beyond the workdir, except protected system paths and the Spark agent's own code. Use with care.",
    "unrestricted": "Access mode: unrestricted. All safeguards are off, including protection of Spark's own code and configuration. Any operation is permitted. Be deliberate: mistakes here can damage the agent itself.",
}


def build_messages(
    *,
    workdir: Path,
    cfg: SparkConfig,
    history: list[ChatMessage],
    memory_block: str | None = None,
) -> list[ChatMessage]:
    system = load_system_prompt() + f"\n\nWorkdir: {workdir.resolve()}"
    mode_hint = MODE_HINTS.get(getattr(cfg.agent, "sandbox_mode", "workspace"))
    if mode_hint:
        system += f"\n{mode_hint}"
    messages: list[ChatMessage] = [ChatMessage(role="system", content=system)]
    agents = load_agents_md(workdir, cfg)
    if agents:
        messages.append(
            ChatMessage(
                role="user",
                content=f"<project_instructions>\n{agents}\n</project_instructions>",
            )
        )
    if memory_block:
        messages.append(ChatMessage(role="user", content=memory_block))
    rest = [m for m in history if m.role != "system"]

    summaries = [m for m in rest if m.role == "summary"]
    recent = [m for m in rest if m.role != "summary"]
    budget = cfg.context.history_budget_chars
    kept: list[ChatMessage] = []
    used = 0
    for msg in reversed(recent):
        size = len(msg.content or "") + sum(len(str(c.arguments)) for c in (msg.tool_calls or []))
        if used + size > budget and kept:
            break
        kept.append(msg)
        used += size
    kept.reverse()
    kept = _collapse_old_images(kept)
    messages.extend(summaries)
    messages.extend(kept)
    return messages


def _collapse_old_images(kept: list[ChatMessage]) -> list[ChatMessage]:
    """Keep images only on the newest image message; older ones become text placeholders to save tokens."""
    last = -1
    for i, m in enumerate(kept):
        if m.images:
            last = i
    out: list[ChatMessage] = []
    for i, m in enumerate(kept):
        if m.images and i != last:
            out.append(
                ChatMessage(
                    role=m.role,
                    content=f"[image attached earlier]\n{m.content or ''}",
                    tool_calls=m.tool_calls,
                    tool_call_id=m.tool_call_id,
                    name=m.name,
                )
            )
        else:
            out.append(m)
    return out


def history_token_usage(
    *,
    cfg: SparkConfig,
    history: list[ChatMessage],
    workdir: Path,
    tool_overhead_tokens: int = 0,
) -> dict:
    """Estimate total request size: system + agents.md + memory slot + history + tool schemas."""
    system = load_system_prompt() + f"\n\nWorkdir: {workdir.resolve()}"
    total = estimate_message_tokens(ChatMessage(role="system", content=system))
    agents = load_agents_md(workdir, cfg)
    if agents:
        total += estimate_message_tokens(ChatMessage(role="user", content=agents))
    total += 512  # memory block slot
    total += sum(estimate_message_tokens(m) for m in history if m.role != "system")
    total += tool_overhead_tokens
    return {
        "used": total,
        "limit": cfg.context.max_context_tokens,
        "percent": round(total * 100 / max(1, cfg.context.max_context_tokens), 1),
    }
