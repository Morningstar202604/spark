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
    shell_timeout_sec: int = Field(default=60, ge=1, le=3600)
    max_tool_rounds: int = Field(default=30, ge=1, le=1000)
    max_repeat_calls: int = Field(default=4, ge=0, le=10000)
    max_turn_tokens: int = Field(default=0, ge=0, le=100_000_000)
    max_output_chars: int = Field(default=8000, ge=200, le=1_000_000)
    show_thinking: bool = True
    show_tools: bool = True
    show_plan: bool = True
    show_context: bool = True
    show_keywords: bool = True
    show_notices: bool = True


class ContextConfig(BaseModel):
    agents_md: str = "AGENTS.md"
    max_fragment_chars: int = Field(default=8000, ge=200, le=1_000_000)
    history_budget_chars: int = Field(default=96000, ge=1000, le=100_000_000)
    max_context_tokens: int = Field(default=32768, ge=1024, le=10_000_000)
    compact_threshold: float = Field(default=0.85, ge=0.1, le=0.99)
    keep_recent_messages: int = Field(default=8, ge=2, le=200)


class McpServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    readonly_tools: list[str] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    enabled: bool = True
    top_k: int = Field(default=6, ge=1, le=50)
    capacity: int = Field(default=500, ge=1, le=100_000)
    embedding_model: str = "text-embedding-3-small"
    auto_extract: bool = True


class ModelProfile(BaseModel):
    id: str = ""
    name: str = ""
    provider: ProviderName = "openai_compat"
    base_url: str = ""
    model: str = ""
    api_key: str = ""


class HookConfig(BaseModel):
    event: str = "pre_tool"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    name: str = ""
    timeout_sec: int = 15


class SparkConfig(BaseModel):
    active_profile_id: str = ""
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    model_profiles: list[ModelProfile] = Field(default_factory=list)
    hooks: list[HookConfig] = Field(default_factory=list)


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
max_repeat_calls = 4
max_turn_tokens = 0

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
    servers = (
        data.pop("mcp", {}).get("servers", [])
        if isinstance(data.get("mcp"), dict)
        else data.pop("mcp_servers", [])
    )
    if servers:
        data["mcp_servers"] = servers
    return data


def _sanitize_project_config(raw: dict[str, Any]) -> dict[str, Any]:
    """A repository-local .spark.toml is untrusted input: it must not be able to
    execute code, hook tools, or redirect credentials to a third-party endpoint."""
    clean = dict(raw)
    clean.pop("mcp_servers", None)
    clean.pop("mcp", None)
    clean.pop("hooks", None)
    clean.pop("model_profiles", None)
    provider = clean.get("provider")
    if isinstance(provider, dict):
        provider = dict(provider)
        provider.pop("base_url", None)
        provider.pop("api_key", None)
        provider.pop("api_key_env", None)
        clean["provider"] = provider
    agent = clean.get("agent")
    if isinstance(agent, dict):
        agent = dict(agent)
        agent.pop("approval", None)
        agent.pop("sandbox_mode", None)
        clean["agent"] = agent
    return clean


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
    from_local_workdir = False
    candidates = []
    if config_path is not None:
        candidates.append(config_path)
    else:
        candidates.extend([workdir / ".spark.toml", default_config_path()])
    for candidate in candidates:
        if candidate.exists():
            chosen = candidate
            raw = _load_toml(candidate)
            from_local_workdir = (
                candidate.name == ".spark.toml"
                and candidate.parent == workdir.resolve()
                if candidate.is_absolute()
                else candidate.name == ".spark.toml"
            )
            break
    if from_local_workdir:
        raw = _sanitize_project_config(raw)
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
    if len(value) <= 12:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


def _toml_str(value: str) -> str:
    return json.dumps(value if value is not None else "", ensure_ascii=False)


def save_config(cfg: SparkConfig, path: Path | None = None) -> Path:
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    key_line = (
        f"api_key = {_toml_str(cfg.provider.api_key or '')}\n"
        if cfg.provider.api_key
        else ""
    )
    mcp_blocks = []
    for server in cfg.mcp_servers:
        mcp_blocks.append(
            "[[mcp.servers]]\n"
            f"name = {_toml_str(server.name)}\n"
            f"command = {_toml_str(server.command)}\n"
            f"args = {json.dumps(server.args)}\n"
            f"readonly_tools = {json.dumps(server.readonly_tools)}\n"
        )
    mcp_section = ("\n" + "\n".join(mcp_blocks)) if mcp_blocks else ""
    profile_blocks = []
    for prof in cfg.model_profiles:
        profile_blocks.append(
            "[[model_profiles]]\n"
            f"id = {_toml_str(prof.id)}\n"
            f"name = {_toml_str(prof.name)}\n"
            f"provider = {_toml_str(prof.provider)}\n"
            f"base_url = {_toml_str(prof.base_url)}\n"
            f"model = {_toml_str(prof.model)}\n"
            f"api_key = {_toml_str(prof.api_key)}\n"
        )
    profiles_section = ("\n" + "\n".join(profile_blocks)) if profile_blocks else ""
    hook_blocks = []
    for hook in cfg.hooks:
        hook_blocks.append(
            "[[hooks]]\n"
            f"event = {_toml_str(hook.event)}\n"
            f"command = {_toml_str(hook.command)}\n"
            f"args = {json.dumps(list(hook.args))}\n"
            f"name = {_toml_str(hook.name)}\n"
            f"timeout_sec = {hook.timeout_sec}\n"
        )
    hooks_section = ("\n" + "\n".join(hook_blocks)) if hook_blocks else ""
    memory_section = (
        f"\n[memory]\nenabled = {str(cfg.memory.enabled).lower()}\n"
        f"top_k = {cfg.memory.top_k}\ncapacity = {cfg.memory.capacity}\n"
        f"embedding_model = {_toml_str(cfg.memory.embedding_model)}\n"
        f"auto_extract = {str(cfg.memory.auto_extract).lower()}\n"
    )
    body = f"""active_profile_id = {_toml_str(cfg.active_profile_id)}
[provider]
name = {_toml_str(cfg.provider.name)}
base_url = {_toml_str(cfg.provider.base_url)}
model = {_toml_str(cfg.provider.model)}
api_key_env = {_toml_str(cfg.provider.api_key_env)}
{key_line}[agent]
approval = {_toml_str(cfg.agent.approval)}
workdir_only = {str(cfg.agent.workdir_only).lower()}
sandbox_mode = {_toml_str(cfg.agent.sandbox_mode)}
protected_paths = {json.dumps(list(cfg.agent.protected_paths))}
shell_timeout_sec = {cfg.agent.shell_timeout_sec}
max_tool_rounds = {cfg.agent.max_tool_rounds}
max_repeat_calls = {cfg.agent.max_repeat_calls}
max_turn_tokens = {cfg.agent.max_turn_tokens}
max_output_chars = {cfg.agent.max_output_chars}
show_thinking = {str(cfg.agent.show_thinking).lower()}
show_tools = {str(cfg.agent.show_tools).lower()}
show_plan = {str(cfg.agent.show_plan).lower()}
show_context = {str(cfg.agent.show_context).lower()}
show_keywords = {str(cfg.agent.show_keywords).lower()}
show_notices = {str(cfg.agent.show_notices).lower()}

[context]
agents_md = {_toml_str(cfg.context.agents_md)}
max_fragment_chars = {cfg.context.max_fragment_chars}
history_budget_chars = {cfg.context.history_budget_chars}
max_context_tokens = {cfg.context.max_context_tokens}
compact_threshold = {cfg.context.compact_threshold}
keep_recent_messages = {cfg.context.keep_recent_messages}{memory_section}{mcp_section}{profiles_section}{hooks_section}
"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
