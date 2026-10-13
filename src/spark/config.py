"""Configuration — built on pydantic-settings.

* ``SparkConfig`` extends :class:`pydantic_settings.BaseSettings` so that every
  field can be overridden via ``SPARK_*`` environment variables out of the box
  (e.g. ``SPARK_AGENT__MAX_TOOL_ROUNDS=50``).
* TOML file loading is preserved via :func:`load_config` which feeds the parsed
  dict into ``SparkConfig.model_validate`` — env vars from pydantic-settings
  then override the TOML values automatically.
* Untrusted project-local ``.spark.toml`` files go through
  :func:`_sanitize_project_config` exactly like before.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast, get_args

import tomlkit
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from spark.errors import ConfigError

logger = logging.getLogger(__name__)

ApprovalMode = Literal[
    "suggest",
    "auto-edit",
    "full-auto",
    "plan-only",
    "approve-plan",
    "manual",
]
"""五档审批模式（业界范式对齐）：

* ``full-auto``   — 全部自动执行（写/执行均 allow），仅 deny 规则可拦截
* ``auto-edit``   — 写工具自动、执行工具逐条确认（历史三档，保留兼容）
* ``suggest``     — 写/执行均提示确认（历史三档）
* ``plan-only``   — 只读允许、写/执行一律不执行（plan mode，仅输出计划）
* ``approve-plan``— 先出计划并等待批准，批准后按 auto-edit 语义自动执行
* ``manual``      — 写/执行逐条人工确认
"""
ProviderName = Literal["openai_compat", "ollama", "mock"]
SandboxMode = Literal["sandbox-only", "workspace", "full-access", "unrestricted"]
# Single source of truth for CLI/config validation — derived from the Literals so
# a mode can never be added to the type without also being accepted at runtime.
APPROVAL_MODES: tuple[str, ...] = get_args(ApprovalMode)
PROVIDER_NAMES: tuple[str, ...] = get_args(ProviderName)
SANDBOX_MODES: tuple[str, ...] = get_args(SandboxMode)


def validate_choice(value: str | None, allowed: tuple[str, ...], label: str) -> str:
    """Validate a user-supplied enum-ish value against the canonical list.

    Unknown values must never reach the policy layer: ``decide()`` only treats
    ``"suggest"`` as the strict mode, so a typo would silently fail *open*.
    """
    if value is None:
        return ""
    if value not in allowed:
        raise ConfigError(f"unknown {label}: {value!r} (choose one of: {', '.join(allowed)})")
    return value


DisplayConfigKey = Literal[
    "show_thinking",
    "show_tools",
    "show_plan",
    "show_context",
    "show_keywords",
    "show_notices",
]
"""Tool-call approval policy and wildcard permission rules."""
PermissionAction = Literal["allow", "prompt", "deny"]


@dataclass
class PermissionRule:
    """A single wildcard permission rule evaluated against tool resources.

    ``resource`` is a glob pattern (``src/foo/*``, ``**/*.py``, ...).
    ``action`` is one of ``allow``, ``prompt``, ``deny``.
    ``tool`` optionally restricts the rule to the named tool(s), also a glob
    (``run_shell``, ``mcp__github__*``).  An empty ``tool`` means "any tool",
    which is how pre-existing rules keep working.
    """

    action: PermissionAction
    resource: str
    tool: str = ""


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
    effort: Literal["low", "medium", "high"] = "medium"
    show_thinking: bool = True
    show_tools: bool = True
    show_plan: bool = True
    show_context: bool = True
    show_keywords: bool = True
    show_notices: bool = True
    # 自定义系统提示词：留空 = 使用内置 prompts/system.md；填写后覆盖默认
    system_prompt: str = ""


class ContextConfig(BaseModel):
    agents_md: str = "AGENTS.md"
    max_fragment_chars: int = Field(default=8000, ge=200, le=1_000_000)
    history_budget_chars: int = Field(default=96000, ge=1000, le=100_000_000)
    max_context_tokens: int = Field(default=32768, ge=1024, le=10_000_000)
    compact_threshold: float = Field(default=0.85, ge=0.1, le=0.99)
    keep_recent_messages: int = Field(default=8, ge=2, le=200)
    # P0-10: tree-sitter repo-map 代码索引注入
    repo_map_enabled: bool = True
    repo_map_max_chars: int = Field(default=6000, ge=500, le=60_000)


class McpTransportConfig(BaseModel):
    """HTTP/SSE transport configuration (for opencode-style MCP servers)."""

    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_sec: int = 30


class McpServerConfig(BaseModel):
    name: str
    command: str = ""  # for stdio transport
    args: list[str] = Field(default_factory=list)  # for stdio transport
    readonly_tools: list[str] = Field(default_factory=list)
    transport: Literal["stdio", "sse", "streamable_http"] = "stdio"
    http: McpTransportConfig | None = None  # for the sse / streamable_http transports
    # P1-9: 第三方 MCP server 最小权限白名单。
    # 空列表 = 向后兼容：该 server 公告的全部工具都暴露（历史行为）。
    # 非空 = 最小权限模式：只注册/调用名字命中任意 glob 的工具（"*" 表示全部），
    # 未声明的工具一律拒绝注册与调用（默认拒绝未声明项）。glob 语义同
    # policy._wildcard_match（'*' 单段、'**' 跨段、'?' 单字符）。
    allowed_tools: list[str] = Field(default_factory=list)


class MemoryConfig(BaseModel):
    enabled: bool = True
    top_k: int = Field(default=6, ge=1, le=50)
    capacity: int = Field(default=500, ge=1, le=100_000)
    embedding_model: str = "text-embedding-3-small"
    auto_extract: bool = True
    rerank_model: str = ""  # 留空 = 不启用重排；填 BAAI/bge-reranker-v2-m3 等本地模型


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
    on_deny: Literal["warn", "block"] = "warn"


class SparkConfig(BaseSettings):
    """Root configuration — native ``SPARK_*`` env-var support via pydantic-settings.

    ``env_nested_delimiter="__"`` means ``SPARK_AGENT__MAX_TOOL_ROUNDS=50`` maps
    to ``agent.max_tool_rounds = 50``.  Extra keys present in neither TOML nor
    env are ignored (``extra="ignore"``) for forward-compatibility.
    """

    model_config = SettingsConfigDict(
        env_prefix="SPARK_",
        env_nested_delimiter="__",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """Only ``$SPARK_HOME/.env`` may supply settings from a file.

        ``SettingsConfigDict(env_file=".env")`` resolves against the *current
        working directory*, which for Spark is the repository being worked on.
        A cloned repo could then ship a ``.env`` that sets
        ``SPARK_AGENT__APPROVAL=full-auto`` / ``SPARK_AGENT__SANDBOX_MODE=unrestricted``
        / ``SPARK_HOOKS`` and hand itself code execution plus a free pass on every
        tool call — the project-level TOML sanitizer would never see it, because
        this channel is not TOML.
        """
        from pydantic_settings import DotEnvSettingsSource

        user_env = default_home() / ".env"
        if not user_env.is_file():
            return (init_settings, env_settings, file_secret_settings)
        cfg = settings_cls.model_config or {}
        return (
            init_settings,
            env_settings,
            DotEnvSettingsSource(
                settings_cls,
                env_file=str(user_env),
                env_file_encoding=cfg.get("env_file_encoding", "utf-8"),
                env_prefix=cfg.get("env_prefix", ""),
                env_nested_delimiter=cfg.get("env_nested_delimiter"),
                case_sensitive=cfg.get("case_sensitive", False),
            ),
            file_secret_settings,
        )

    active_profile_id: str = ""
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)
    model_profiles: list[ModelProfile] = Field(default_factory=list)
    hooks: list[HookConfig] = Field(default_factory=list)
    permission_rules: list[PermissionRule] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# File-system helpers (unchanged)
# ---------------------------------------------------------------------------


def default_home() -> Path:
    # SPARK_HOME 在调用时动态生效（tests/conftest.py 依赖此契约隔离测试，
    # 避免写坏真实 ~/.spark）；勿在 import 时固化路径。
    env = os.environ.get("SPARK_HOME")
    if env and env.strip():
        return Path(env).expanduser()
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
sandbox_mode = "workspace"
shell_timeout_sec = 60
max_tool_rounds = 30
max_repeat_calls = 4
max_turn_tokens = 0

[context]
agents_md = "AGENTS.md"
max_fragment_chars = 8000
history_budget_chars = 96000
max_context_tokens = 32768
compact_threshold = 0.85
keep_recent_messages = 8

[memory]
enabled = true
top_k = 6
capacity = 500
embedding_model = "text-embedding-3-small"
auto_extract = true

# MCP server — stdio transport example (uncomment to enable)
# [[mcp.servers]]
# name = "my-stdio-server"
# command = "npx"
# args = ["-y", "@example/mcp-server"]
# transport = "stdio"
# readonly_tools = []
# # P1-9 最小权限白名单（可选）：非空时只暴露/调用命中 glob 的工具，
# # 未声明的一律拒绝；["*"] 显式放行全部。空 = 暴露 server 公告的全部工具。
# allowed_tools = ["list_*", "get_*"]

# MCP server — SSE transport example (uncomment to enable)
# [[mcp.servers]]
# name = "my-sse-server"
# transport = "sse"
# readonly_tools = []
# [mcp.servers.http]
# url = "http://localhost:3000/sse"
# headers = {Authorization = "Bearer <token>"}
# timeout_sec = 30

# Model profiles (uncomment to enable multi-model switching)
# [[model_profiles]]
# id = "gpt-4o"
# name = "GPT-4o"
# provider = "openai_compat"
# base_url = "https://api.openai.com/v1"
# model = "gpt-4o"
# api_key = ""

# Pre/post hooks (uncomment to enable shell hooks)
# [[hooks]]
# event = "pre_tool"
# command = "echo"
# args = ["tool about to run"]
# name = "log-hook"
# timeout_sec = 15
# on_deny = "warn"

# Permission rules (uncomment to enable, last match wins)
# [[permission_rules]]
# action = "allow"
# resource = "src/**/*.py"
# [[permission_rules]]
# action = "deny"
# resource = "/etc/**"
""",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# TOML loading helpers (unchanged)
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict[str, Any]:
    """Read a TOML config file. Malformed TOML surfaces as :class:`ConfigError`
    so callers get a friendly message + exit code 2 instead of a raw
    ``tomllib.TOMLDecodeError`` traceback."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
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
    execute code, hook tools, self-authorise permissions, or redirect credentials
    to a third-party endpoint.

    Permission rules are stripped as well — otherwise a cloned repository could
    ship ``[[permission_rules]] action="allow" resource="**"`` and grant itself
    permission to run any shell command without asking the user.
    """
    clean = dict(raw)
    clean.pop("mcp_servers", None)
    clean.pop("mcp", None)
    clean.pop("hooks", None)
    clean.pop("model_profiles", None)
    clean.pop("permission_rules", None)
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
        # ``workdir_only`` and ``protected_paths`` decide whether the agent may
        # leave the repository at all — a repo must not be able to widen them.
        agent.pop("workdir_only", None)
        agent.pop("protected_paths", None)
        clean["agent"] = agent
    # ``context.agents_md`` controls which file is injected into the system
    # prompt — a malicious repo could point it at a sensitive file.  The
    # built-in path guard (``load_agents_md``) rejects separators/traversal, but
    # we strip it from untrusted project configs as defense-in-depth.
    context = clean.get("context")
    if isinstance(context, dict):
        context = dict(context)
        if "agents_md" in context:
            context["agents_md"] = "AGENTS.md"
        clean["context"] = context
    return clean


# ---------------------------------------------------------------------------
# Config assembly
# ---------------------------------------------------------------------------


def _merge_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Section-wise merge: nested dicts merge, scalars and lists replace.

    A repository-local ``.spark.toml`` is an *overlay* on the user's global
    config, never a replacement — otherwise opening a repo would silently drop
    the user's own ``protected_paths``, hooks and permission rules.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _is_repo_local(path: Path, workdir: Path) -> bool:
    """True when *path* is a ``.spark.toml`` living in *workdir*.

    Compares *resolved* parents: a symlinked or junctioned workdir must not be
    able to make its ``.spark.toml`` look like the global config (and vice
    versa) purely by spelling the path differently.
    """
    if path.name != ".spark.toml":
        return False
    try:
        return path.resolve().parent == workdir.resolve()
    except OSError:
        return path.parent == workdir


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
    if config_path is not None:
        chosen = config_path
        raw = _load_toml(config_path)
        if _is_repo_local(config_path, workdir):
            raw = _sanitize_project_config(raw)
    else:
        global_path = default_config_path()
        if global_path.exists():
            chosen = global_path
            raw = _load_toml(global_path)
        repo_path = workdir / ".spark.toml"
        if repo_path.exists() and not _is_repo_local(global_path, workdir):
            raw = _merge_config(raw, _sanitize_project_config(_load_toml(repo_path)))
            if chosen is None:
                chosen = repo_path

    # SparkConfig (BaseSettings) validates TOML data *and* reads SPARK_* env vars
    # automatically.  Programmatic CLI overrides are applied below.
    try:
        cfg = SparkConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Invalid config file: {chosen or 'defaults'}") from exc

    if approval:
        cfg.agent.approval = cast(
            ApprovalMode, validate_choice(approval, APPROVAL_MODES, "approval mode")
        )
    if sandbox_mode:
        cfg.agent.sandbox_mode = cast(
            SandboxMode, validate_choice(sandbox_mode, SANDBOX_MODES, "sandbox mode")
        )
    if model:
        cfg.provider.model = model
    elif os.environ.get("SPARK_MODEL"):
        cfg.provider.model = os.environ["SPARK_MODEL"]
    if provider:
        cfg.provider.name = cast(
            ProviderName, validate_choice(provider, PROVIDER_NAMES, "provider")
        )
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
        raise ConfigError(
            f"Missing API key: neither config's provider.api_key nor "
            f"environment variable '{env_name}' is set. "
            f"Fix: run 'spark init' or set {env_name}=<your-key>."
        )
    return key


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


# ---------------------------------------------------------------------------
# Config serialisation
# ---------------------------------------------------------------------------


def save_config(cfg: SparkConfig, path: Path | None = None) -> Path:
    """Persist configuration as TOML using the official ``tomlkit`` library.

    ``tomlkit`` produces spec-compliant TOML with correct quoting, escaping of
    special characters, and proper handling of arrays and inline tables — none of
    which we had to invent.
    """
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = tomlkit.document()
    doc.add("active_profile_id", cfg.active_profile_id or "")
    doc.add(
        tomlkit.comment("Spark agent configuration — edit with: spark config set <key> <value>")
    )
    doc.add(tomlkit.nl())

    # [provider]
    provider = tomlkit.table()
    provider.add("name", cfg.provider.name)
    provider.add("base_url", cfg.provider.base_url or "")
    provider.add("model", cfg.provider.model or "")
    provider.add("api_key_env", cfg.provider.api_key_env or "")
    if cfg.provider.api_key:
        provider.add("api_key", cfg.provider.api_key)
    doc.add("provider", provider)

    # [agent]
    agent = tomlkit.table()
    agent.add("approval", cfg.agent.approval)
    agent.add("workdir_only", cfg.agent.workdir_only)
    agent.add("sandbox_mode", cfg.agent.sandbox_mode)
    agent.add("protected_paths", list(cfg.agent.protected_paths))
    agent.add("shell_timeout_sec", cfg.agent.shell_timeout_sec)
    agent.add("max_tool_rounds", cfg.agent.max_tool_rounds)
    agent.add("max_repeat_calls", cfg.agent.max_repeat_calls)
    agent.add("max_turn_tokens", cfg.agent.max_turn_tokens)
    agent.add("max_output_chars", cfg.agent.max_output_chars)
    agent.add("show_thinking", cfg.agent.show_thinking)
    agent.add("show_tools", cfg.agent.show_tools)
    agent.add("show_plan", cfg.agent.show_plan)
    agent.add("show_context", cfg.agent.show_context)
    agent.add("show_keywords", cfg.agent.show_keywords)
    agent.add("show_notices", cfg.agent.show_notices)
    agent.add("effort", cfg.agent.effort)
    doc.add("agent", agent)

    # [context]
    context = tomlkit.table()
    context.add("agents_md", cfg.context.agents_md)
    context.add("max_fragment_chars", cfg.context.max_fragment_chars)
    context.add("history_budget_chars", cfg.context.history_budget_chars)
    context.add("max_context_tokens", cfg.context.max_context_tokens)
    context.add("compact_threshold", cfg.context.compact_threshold)
    context.add("keep_recent_messages", cfg.context.keep_recent_messages)
    doc.add("context", context)

    # [memory]
    memory = tomlkit.table()
    memory.add("enabled", cfg.memory.enabled)
    memory.add("top_k", cfg.memory.top_k)
    memory.add("capacity", cfg.memory.capacity)
    memory.add("embedding_model", cfg.memory.embedding_model or "")
    memory.add("auto_extract", cfg.memory.auto_extract)
    doc.add("memory", memory)

    # [[mcp.servers]]
    if cfg.mcp_servers:
        mcp_aot = tomlkit.aot()
        for server in cfg.mcp_servers:
            srv_table = tomlkit.table(is_super_table=True)
            srv_table.add("name", server.name)
            if server.transport == "sse":
                srv_table.add("transport", "sse")
                srv_table.add("readonly_tools", list(server.readonly_tools))
                if server.allowed_tools:
                    srv_table.add("allowed_tools", list(server.allowed_tools))
                if server.http:
                    http = tomlkit.table()
                    http.add("url", server.http.url)
                    if server.http.headers:
                        http.add("headers", server.http.headers)
                    http.add("timeout_sec", server.http.timeout_sec)
                    srv_table.add("http", http)
            else:
                srv_table.add("command", server.command)
                srv_table.add("args", list(server.args))
                srv_table.add("readonly_tools", list(server.readonly_tools))
                if server.allowed_tools:
                    srv_table.add("allowed_tools", list(server.allowed_tools))
            mcp_aot.append(srv_table)
        doc.add("mcp", tomlkit.table().add("servers", mcp_aot))

    # [[model_profiles]]
    if cfg.model_profiles:
        prof_aot = tomlkit.aot()
        for prof in cfg.model_profiles:
            pt = tomlkit.table()
            pt.add("id", prof.id)
            pt.add("name", prof.name)
            pt.add("provider", prof.provider)
            pt.add("base_url", prof.base_url or "")
            pt.add("model", prof.model or "")
            pt.add("api_key", prof.api_key or "")
            prof_aot.append(pt)
        doc.add("model_profiles", prof_aot)

    # [[hooks]]
    if cfg.hooks:
        hook_aot = tomlkit.aot()
        for hook in cfg.hooks:
            ht = tomlkit.table()
            ht.add("event", hook.event)
            ht.add("command", hook.command)
            ht.add("args", list(hook.args))
            ht.add("name", hook.name or "")
            ht.add("timeout_sec", hook.timeout_sec)
            ht.add("on_deny", hook.on_deny or "")
            hook_aot.append(ht)
        doc.add("hooks", hook_aot)

    # [[permission_rules]]
    if cfg.permission_rules:
        pr_aot = tomlkit.aot()
        for rule in cfg.permission_rules:
            rt = tomlkit.table()
            rt.add("action", rule.action)
            rt.add("resource", rule.resource)
            pr_aot.append(rt)
        doc.add("permission_rules", pr_aot)

    output = tomlkit.dumps(doc)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(output, encoding="utf-8")  # noqa: E501
    os.replace(tmp, path)
    _harden_permissions(path)
    return path


def _harden_permissions(f: Path) -> None:
    """收紧配置文件权限（内含 API Key / 访问令牌）。

    POSIX：chmod 600。Windows：chmod 只拨只读位、不限制其他账户读取，
    必须用 icacls 移除继承并只保留当前用户 SID。全部失败安全。
    """
    if sys.platform == "win32":
        sid = _win_user_sid()
        if sid:
            try:
                subprocess.run(
                    ["icacls", str(f), "/inheritance:r", "/grant:r", f"*{sid}:F"],
                    capture_output=True,
                    timeout=30,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return
            except (OSError, subprocess.SubprocessError):
                pass
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


_WIN_SID: str | None = None


def _win_user_sid() -> str | None:
    """当前用户 SID（进程内缓存）；取不到返回 None（调用方回退 chmod）。"""
    global _WIN_SID
    if _WIN_SID is None:
        try:
            out = subprocess.run(
                ["whoami", "/user", "/fo", "csv", "/nh"],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            ).stdout
            m = re.search(r"S-1-\d[\d-]*\d", out)
            _WIN_SID = m.group(0) if m else ""
        except (OSError, subprocess.SubprocessError):
            _WIN_SID = ""
    return _WIN_SID or None


def win_acl_restricted(f: Path) -> bool:
    """Windows：文件 ACL 是否已收紧到只剩当前用户一条 ACE（供 doctor 展示）。"""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(
            ["icacls", str(f)],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    aces = [ln for ln in out.splitlines() if ":(" in ln]
    return len(aces) == 1
