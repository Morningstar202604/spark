from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from spark.errors import ConfigError

ApprovalMode = Literal["suggest", "auto-edit", "full-auto"]
ProviderName = Literal["openai_compat", "ollama", "mock"]
SandboxMode = Literal["sandbox-only", "workspace", "full-access", "unrestricted"]
DisplayConfigKey = Literal[
    "show_thinking",
    "show_tools",
    "show_plan",
    "show_context",
    "show_keywords",
    "show_notices",
]


class ProviderConfig(BaseModel):
    name: ProviderName = "openai_compat"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key_env: str = "SPARK_API_KEY"
    api_key: str | None = None


class AgentConfig(BaseModel):
    approval: ApprovalMode = "suggest"
    workdir_only: bool = True
    sandbox_mode: SandboxMode = "workspace"
    protected_paths: list[str] = Field(default_factory=list)
    shell_timeout_sec: int = 60
    max_tool_rounds: int = 30
    max_output_chars: int = 8000
    show_thinking: bool = True
    show_tools: bool = True
    show_plan: bool = True
    show_context: bool = True
    show_keywords: bool = True
    show_notices: bool = True


class ContextConfig(BaseModel):
    agents_md: str = "AGENTS.md"
    max_fragment_chars: int = 8000
    history_budget_chars: int = 96000
    max_context_tokens: int = 32768
    compact_threshold: float = 0.85
    keep_recent_messages: int = 8


class McpServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    readonly_tools: list[str] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    enabled: bool = True
    top_k: int = 6
    capacity: int = 500
    embedding_model: str = "text-embedding-3-small"
    auto_extract: bool = True


class ModelProfile(BaseModel):
    id: str = ""
    name: str = ""
    provider: ProviderName = "openai_compat"
    base_url: str = ""
    model: str = ""
    api_key: str = ""


class SparkConfig(BaseModel):
    active_profile_id: str = ""
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    model_profiles: list[ModelProfile] = Field(default_factory=list)


def default_home() -> Path:
    return Path.home() / ".spark"


def default_config_path() -> Path:
    return default_home() / "config.toml"


def ensure_home() -> Path:
    home = default_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def write_config_template(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.write_text(
        """[provider]
name = "openai_compat"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key_env = "SPARK_API_KEY"

[agent]
approval = "suggest"
workdir_only = true
shell_timeout_sec = 60
max_tool_rounds = 30

[context]
agents_md = "AGENTS.md"
max_fragment_chars = 8000
history_budget_chars = 96000
""",
        encoding="utf-8",
    )


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    servers = data.pop("mcp", {}).get("servers", []) if isinstance(data.get("mcp"), dict) else data.pop("mcp_servers", [])
    if servers:
        data["mcp_servers"] = servers
    return data


def load_config(
    *,
    config_path: Path | None = None,
    workdir: Path,
    approval: ApprovalMode | None = None,
    model: str | None = None,
    provider: ProviderName | None = None,
    sandbox_mode: SandboxMode | None = None,
) -> SparkConfig:
    raw: dict[str, Any] = {}
    chosen: Path | None = None
    candidates = []
    if config_path is not None:
        candidates.append(config_path)
    else:
        candidates.extend([workdir / ".spark.toml", default_config_path()])
    for candidate in candidates:
        if candidate.exists():
            chosen = candidate
            raw = _load_toml(candidate)
            break
    try:
        cfg = SparkConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Invalid config file: {chosen or 'defaults'}") from exc
    if approval:
        cfg.agent.approval = approval
    if sandbox_mode:
        cfg.agent.sandbox_mode = sandbox_mode
    if model:
        cfg.provider.model = model
    elif os.environ.get("SPARK_MODEL"):
        cfg.provider.model = os.environ["SPARK_MODEL"]
    if provider:
        cfg.provider.name = provider
    if os.environ.get("SPARK_BASE_URL"):
        cfg.provider.base_url = os.environ["SPARK_BASE_URL"]
    return cfg


def require_api_key(cfg: SparkConfig) -> str | None:
    if cfg.provider.api_key:
        return cfg.provider.api_key
    if cfg.provider.name in {"mock", "ollama"}:
        env_name = cfg.provider.api_key_env
        return os.environ.get(env_name) or os.environ.get("SPARK_API_KEY") or "ollama"
    env_name = cfg.provider.api_key_env
    key = os.environ.get(env_name) or os.environ.get("SPARK_API_KEY")
    if not key:
        raise ConfigError(f"Missing User API Key in environment variable {env_name}")
    return key


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


def save_config(cfg: SparkConfig, path: Path | None = None) -> Path:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    key_line = f'api_key = "{cfg.provider.api_key}"\n' if cfg.provider.api_key else ""
    mcp_blocks = []
    for server in cfg.mcp_servers:
        mcp_blocks.append(
            "[[mcp.servers]]\n"
            f'name = "{server.name}"\n'
            f'command = "{server.command}"\n'
            f"args = {json.dumps(server.args)}\n"
            f"readonly_tools = {json.dumps(server.readonly_tools)}\n"
        )
    mcp_section = ("\n" + "\n".join(mcp_blocks)) if mcp_blocks else ""
    profile_blocks = []
    for prof in cfg.model_profiles:
        profile_blocks.append(
            "[[model_profiles]]\n"
            f'id = "{prof.id}"\n'
            f'name = "{prof.name}"\n'
            f'provider = "{prof.provider}"\n'
            f'base_url = "{prof.base_url}"\n'
            f'model = "{prof.model}"\n'
            f'api_key = "{prof.api_key}"\n'
        )
    profiles_section = ("\n" + "\n".join(profile_blocks)) if profile_blocks else ""
    memory_section = (
        f"\n[memory]\nenabled = {str(cfg.memory.enabled).lower()}\n"
        f"top_k = {cfg.memory.top_k}\ncapacity = {cfg.memory.capacity}\n"
        f'embedding_model = "{cfg.memory.embedding_model}"\n'
        f"auto_extract = {str(cfg.memory.auto_extract).lower()}\n"
    )
    path.write_text(
        f"""active_profile_id = "{cfg.active_profile_id}"
[provider]
name = "{cfg.provider.name}"
base_url = "{cfg.provider.base_url}"
model = "{cfg.provider.model}"
api_key_env = "{cfg.provider.api_key_env}"
{key_line}
[agent]
approval = "{cfg.agent.approval}"
workdir_only = {str(cfg.agent.workdir_only).lower()}
shell_timeout_sec = {cfg.agent.shell_timeout_sec}
max_tool_rounds = {cfg.agent.max_tool_rounds}
max_output_chars = {cfg.agent.max_output_chars}

[context]
agents_md = "{cfg.context.agents_md}"
max_fragment_chars = {cfg.context.max_fragment_chars}
history_budget_chars = {cfg.context.history_budget_chars}{memory_section}{mcp_section}{profiles_section}
""",
        encoding="utf-8",
    )
    return path
