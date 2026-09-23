from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from spark.config import (
    SparkConfig,
    default_home,
    load_config,
    mask_secret,
    save_config,
    write_config_template,
)

SECRET_FIELDS = {"api_key"}

SETTABLE: dict[str, tuple[str, type]] = {
    "provider.name": ("provider.name", str),
    "provider.base_url": ("provider.base_url", str),
    "provider.model": ("provider.model", str),
    "provider.api_key": ("provider.api_key", str),
    "provider.api_key_env": ("provider.api_key_env", str),
    "agent.approval": ("agent.approval", str),
    "agent.workdir_only": ("agent.workdir_only", bool),
    "agent.sandbox_mode": ("agent.sandbox_mode", str),
    "agent.shell_timeout_sec": ("agent.shell_timeout_sec", int),
    "agent.max_tool_rounds": ("agent.max_tool_rounds", int),
    "agent.max_repeat_calls": ("agent.max_repeat_calls", int),
    "agent.max_turn_tokens": ("agent.max_turn_tokens", int),
    "agent.max_output_chars": ("agent.max_output_chars", int),
    "context.agents_md": ("context.agents_md", str),
    "context.max_context_tokens": ("context.max_context_tokens", int),
    "context.compact_threshold": ("context.compact_threshold", float),
    "context.keep_recent_messages": ("context.keep_recent_messages", int),
    "memory.enabled": ("memory.enabled", bool),
    "memory.top_k": ("memory.top_k", int),
    "memory.capacity": ("memory.capacity", int),
}

APPROVAL_MODES = {"suggest", "auto-edit", "full-auto"}
SANDBOX_MODES = {"sandbox-only", "workspace", "full-access", "unrestricted"}

BOOL_WORDS = {
    "true": True,
    "yes": True,
    "on": True,
    "1": True,
    "false": False,
    "no": False,
    "off": False,
    "0": False,
}


class SetupError(Exception):
    pass


BRAND = "◆ Spark"
TAGLINE = "本地编程智能体 · 代码在你手，密钥在你手"


def banner(title: str) -> str:
    return f"{BRAND} [Ember] — {title}"


def version_line(detail: str) -> str:
    return f"{BRAND} {detail}\n  {TAGLINE}"


def config_target_path(config: Path | None) -> Path:
    return config or (default_home() / "config.toml")


def read_config(config: Path | None, workdir: Path) -> SparkConfig:
    return load_config(config_path=config, workdir=workdir)


def apply_setup(
    cfg: SparkConfig,
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    provider: str | None,
    approval: str | None,
    sandbox_mode: str | None,
) -> list[str]:
    """Apply non-empty values onto cfg; return the list of changed field names."""
    changed: list[str] = []
    if provider:
        cfg.provider.name = provider  # type: ignore[assignment]
        changed.append("provider.name")
    if base_url:
        cfg.provider.base_url = base_url.rstrip("/")
        changed.append("provider.base_url")
    if model:
        cfg.provider.model = model
        changed.append("provider.model")
    if api_key:
        cfg.provider.api_key = api_key
        changed.append("provider.api_key")
    if approval:
        if approval not in APPROVAL_MODES:
            raise SetupError(
                f"未知审批模式 {approval!r}，可选：{', '.join(sorted(APPROVAL_MODES))}"
            )
        cfg.agent.approval = approval  # type: ignore[assignment]
        changed.append("agent.approval")
    if sandbox_mode:
        if sandbox_mode not in SANDBOX_MODES:
            raise SetupError(
                f"未知访问级别 {sandbox_mode!r}，可选：{', '.join(sorted(SANDBOX_MODES))}"
            )
        cfg.agent.sandbox_mode = sandbox_mode  # type: ignore[assignment]
        changed.append("agent.sandbox_mode")
    return changed


def save(cfg: SparkConfig, config: Path | None) -> Path:
    path = config_target_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    return save_config(cfg, path)


def ensure_skeleton(config: Path | None) -> Path:
    path = config_target_path(config)
    write_config_template(path)
    return path


def config_as_dict(cfg: SparkConfig) -> dict[str, Any]:
    return {
        "provider": {
            "name": cfg.provider.name,
            "base_url": cfg.provider.base_url,
            "model": cfg.provider.model,
            "api_key_env": cfg.provider.api_key_env,
            "api_key": mask_secret(cfg.provider.api_key or ""),
            "has_api_key": bool(cfg.provider.api_key),
        },
        "agent": cfg.agent.model_dump(),
        "context": cfg.context.model_dump(),
        "memory": cfg.memory.model_dump(),
        "mcp_servers": [s.model_dump() for s in cfg.mcp_servers],
        "hooks": [h.model_dump() for h in cfg.hooks],
    }


def render_config_text(cfg: SparkConfig) -> str:
    data = config_as_dict(cfg)
    lines: list[str] = []
    for section in ("provider", "agent", "context", "memory"):
        lines.append(f"[{section}]")
        for key, value in data[section].items():
            if isinstance(value, bool):
                shown = "true" if value else "false"
            elif value is None:
                shown = "(未设置)"
            else:
                shown = str(value)
            lines.append(f"  {key} = {shown}")
    if data["mcp_servers"]:
        lines.append(f"[mcp_servers] 共 {len(data['mcp_servers'])} 个")
    if data["hooks"]:
        lines.append(f"[hooks] 共 {len(data['hooks'])} 个")
    return "\n".join(lines)


def parse_value(key: str, raw: str) -> Any:
    _, kind = SETTABLE[key]
    text = raw.strip()
    if kind is bool:
        lowered = text.lower()
        if lowered not in BOOL_WORDS:
            raise SetupError(f"{key} 需要 true/false，收到 {raw!r}")
        return BOOL_WORDS[lowered]
    if kind is int:
        try:
            return int(text)
        except ValueError as exc:
            raise SetupError(f"{key} 需要整数，收到 {raw!r}") from exc
    if kind is float:
        try:
            return float(text)
        except ValueError as exc:
            raise SetupError(f"{key} 需要数字，收到 {raw!r}") from exc
    return text


def set_value(cfg: SparkConfig, key: str, raw: str) -> None:
    if key not in SETTABLE:
        raise SetupError(f"未知配置项 {key}")
    attr_path, _ = SETTABLE[key]
    value = parse_value(key, raw)
    section, field = attr_path.split(".")
    target = getattr(cfg, section)
    current = getattr(target, field)
    if isinstance(current, float) and isinstance(value, int):
        value = float(value)
    if field == "approval" and value not in APPROVAL_MODES:
        raise SetupError(f"未知审批模式 {value!r}")
    if field == "sandbox_mode" and value not in SANDBOX_MODES:
        raise SetupError(f"未知访问级别 {value!r}")
    if key in SECRET_FIELDS:
        value = str(value) or None
    setattr(target, field, value)


def check_health(
    cfg: SparkConfig, *, workdir: Path, config_path: Path | None
) -> list[tuple[str, str, str]]:
    """Return (level, message, fix) rows describing configuration health."""
    rows: list[tuple[str, str, str]] = []
    if not workdir.exists():
        rows.append(
            (
                "bad",
                f"工作目录不存在：{workdir}",
                "创建目录或用 --workdir 指定其他目录",
            )
        )
    elif not workdir.is_dir():
        rows.append(
            ("bad", f"工作目录不是文件夹：{workdir}", "用 --workdir 指定文件夹")
        )
    else:
        rows.append(("ok", f"工作目录可用：{workdir}", ""))

    if cfg.provider.name == "mock":
        rows.append(
            (
                "warn",
                "当前使用 mock 模型，不会真实调用大模型",
                "运行 spark init 配置真实模型",
            )
        )
    elif not cfg.provider.api_key and not _env_key(cfg):
        rows.append(
            (
                "bad",
                "缺少 API Key，模型无法调用",
                "运行 spark init 填写密钥，或设置环境变量 " + cfg.provider.api_key_env,
            )
        )
    else:
        rows.append(("ok", f"模型凭据已就绪（{cfg.provider.model}）", ""))
    rows.append(("ok", f"接口地址：{cfg.provider.base_url}", ""))
    rows.append(("ok", f"配置文件：{config_path}", ""))
    rows.append(
        (
            "warn",
            "审批模式为 suggest 时，spark exec 无法使用",
            "自动化请加 --approval auto-edit 或 full-auto",
        )
        if cfg.agent.approval == "suggest"
        else ("ok", f"审批模式：{cfg.agent.approval}", "")
    )
    return rows


def _env_key(cfg: SparkConfig) -> str:
    import os

    return (
        os.environ.get(cfg.provider.api_key_env)
        or os.environ.get("SPARK_API_KEY")
        or ""
    )


def friendly_config_error(cfg: SparkConfig) -> str:
    """Turn a missing-credential failure into actionable guidance."""
    if cfg.provider.name == "mock":
        return (
            "当前配置是 mock 模型（不会真实调用）。\n"
            "下一步：运行 spark init 填写真实模型地址、模型名和 API Key。"
        )
    if not cfg.provider.api_key and not _env_key(cfg):
        return (
            "缺少 API Key，无法连接模型。\n"
            "下一步（二选一）：\n"
            "  1) 运行 spark init 交互式填写，密钥会保存到本机配置文件\n"
            f"  2) 设置环境变量 {cfg.provider.api_key_env} 后重试"
        )
    return "配置看起来完整，但模型调用仍失败；请运行 spark doctor 查看详细诊断。"


def render_health(rows: list[tuple[str, str, str]], *, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(
            [
                {"level": level, "message": message, "fix": fix}
                for level, message, fix in rows
            ],
            ensure_ascii=False,
            indent=2,
        )
    marks = {"ok": "[ok]", "warn": "[warn]", "bad": "[bad]"}
    lines: list[str] = []
    for level, message, fix in rows:
        lines.append(f"{marks.get(level, '[?]')} {message}")
        if fix and level != "ok":
            lines.append(f"       → {fix}")
    return "\n".join(lines)


def health_exit_code(rows: list[tuple[str, str, str]]) -> int:
    return 1 if any(level == "bad" for level, _, _ in rows) else 0
