"""配置层：tomlkit 序列化、权限收紧、国产模型预设、随机访问令牌。

与旧版的关键差异：
- 目录基于 Path.home() 动态计算，不再硬编码 /root。
- 用 tomlkit 序列化/反序列化，不再手工拼 TOML 字符串。
- 密钥落盘后 chmod 600。
- 内置国产模型预设，开箱即用。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import tomlkit


def config_dir() -> Path:
    """配置目录：默认 ~/.spark；可用环境变量 SPARK_HOME 覆盖（测试隔离用）。"""
    return Path(os.environ.get("SPARK_HOME") or (Path.home() / ".spark"))


def config_file() -> Path:
    return config_dir() / "config.toml"

# 国产模型预设（OpenAI 兼容协议，2026-09 现役型号，已剔除下线/弃用型号）。
# 说明：DeepSeek 的 deepseek-chat/reasoner 已于 2026-07-24 弃用（现役 v4-pro/flash）；
# Kimi 的 k2 系列已下线（现役 kimi-k3）；moonshot-v1 已下线。
PRESETS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek V4 Pro",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-pro",  # 现役正式版，Agent 能力增强；思考/非思考由调用参数控制
        "api_key": "",
    },
    "deepseek-flash": {
        "label": "DeepSeek V4 Flash（快/省）",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-flash",  # 原名 v4-flash，2026 起官方名为 deepseek-flash
        "api_key": "",
    },
    "qwen": {
        "label": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",  # 官方别名，自动指向现役 qwen3 系列
        "api_key": "",
    },
    "glm": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.6",
        "api_key": "",
    },
    "kimi": {
        "label": "Kimi K3",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "kimi-k3",  # 现役旗舰，1M 上下文，原生推理
        "api_key": "",
    },
    "unisound": {
        "label": "云知声 Unisound",
        "base_url": "https://maas-api.unisound.com/v1",
        "model": "u2-flash",  # 实测型号：u2-flash（快），同系 u2-pro 更稳
        "api_key": "",
    },
    "doubao": {
        "label": "豆包（火山方舟）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-1-5-pro-32k",
        "api_key": "",
    },
    "ollama": {
        "label": "Ollama 本地",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen3-coder",  # 需先在 Ollama 拉取：ollama pull qwen3-coder
        "api_key": "ollama",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "api_key": "",
    },
    "mock": {
        "label": "演示模式（无需密钥）",
        "base_url": "",
        "model": "mock",
        "api_key": "",
    },
}

APPROVAL_MODES = ("suggest", "auto-edit", "full-auto", "plan")

# 环境变量密钥映射：provider → 常见 <PROVIDER>_API_KEY（与主流 agent 生态一致）。
_PROVIDER_ENV: dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "deepseek-flash": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "unisound": "UNISOUND_API_KEY",
    "doubao": "ARK_API_KEY",
    "ollama": "OLLAMA_API_KEY",
}


def _defaults() -> dict:
    return {
        "provider": "mock",
        "base_url": "",
        "model": "mock",
        "api_key": "",
        "workdir": str(Path.cwd()),
        "approval_mode": "suggest",
        "max_context_tokens": 32000,
        "token": "",
        # 多模型路由：model_fast 填写快速模型名（如 deepseek-flash），
        # 简单任务自动走它、复杂任务走主模型；留空 = 不启用路由。
        "model_fast": "",
        # 备用模型（故障切换）：主模型不可用时自动切换；留空 = 不启用 fallback。
        "fallback_model": "",
        # 语义记忆：off=仅关键词检索（默认，零依赖）/ api=火山方舟 doubao-embedding / local=本地模型
        "memory_embedding": "off",
        "memory_embed_model": "",
        # 独立语义嵌入端点（api 模式可选）：当主 provider 无 /embeddings 接口（如 DeepSeek/通义）
        # 时，可单独配火山方舟等 embed 端点；留空则复用主模型的 base_url/api_key。
        "embed_base_url": "",
        "embed_api_key": "",
        "mcp_servers": [],
        # ---- LLM 摘要（opt-in）：compact_summary = off（默认零隐性调用）/ llm（更高质量） ----
        "compact_summary": "off",
        # ---- 高级可调项（全部可从 Web 设置控制） ----
        "system_prompt": "",  # 自定义系统提示词；空 = 内置默认。支持 {workdir} {protected} 占位符
        "protected_paths": [],  # 额外保护路径（list[str]）：这些路径下永远拒绝写入
        "max_turns": 25,  # 单次对话最大工具轮次
        "tool_timeout": 180,  # 单个工具执行超时（秒）
        "auto_verify": True,  # apply_patch 成功后自动跑 pytest 验证（可关）
        "temperature": "",  # 采样温度；空 = 不传给模型（用服务端默认）
        "max_tokens": "",  # 单次回复最大 tokens；空 = 不传
        "route_enabled": True,  # 多模型路由开关（model_fast 非空时才实际生效）
        "route_keywords": "",  # 自定义"强任务"关键词（逗号/空格/换行分隔）；空 = 内置词表
        "usage_pricing": {},  # 成本单价覆盖：{模型名: {"input": 元/M, "output": 元/M}}
        "proxy": "",  # HTTP(S) 代理，如 http://127.0.0.1:7890；空 = 不设代理（尊重环境变量）
    }


# ---------------------------------------------------------------------------
# Schema 校验：白名单字段 + 类型 / 取值约束
# （运行时防御：用户手滑写了非法值时第一时间报出来，而不是静默用默认值跑飞）
# ---------------------------------------------------------------------------

_SCHEMA: dict[str, tuple[type | tuple[type, ...], set[str] | frozenset[str] | None]] = {
    # field → (expected_type_or_tuple, validator_or_None)
    "provider": (str, None),
    "base_url": (str, None),
    "model": (str, None),
    "api_key": (str, None),
    "workdir": (str, None),
    "approval_mode": (str, set(APPROVAL_MODES)),
    "max_context_tokens": (int, None),
    "token": (str, None),
    "model_fast": (str, None),
    "fallback_model": (str, None),
    "memory_embedding": (str, {"off", "api", "local"}),
    "memory_embed_model": (str, None),
    "embed_base_url": (str, None),
    "embed_api_key": (str, None),
    "mcp_servers": (list, None),
    "compact_summary": (str, {"off", "llm"}),
    "system_prompt": (str, None),
    "protected_paths": (list, None),
    "max_turns": (int, None),
    "tool_timeout": ((int, float), None),
    "auto_verify": (bool, None),
    "temperature": (str, None),
    "max_tokens": (str, None),
    "route_enabled": (bool, None),
    "route_keywords": (str, None),
    "usage_pricing": (dict, None),
    "proxy": (str, None),
}


def _validate_and_coerce(cfg: dict, source: str = "config") -> list[str]:
    """校验并（尽可能）修正配置文件中的字段。

    返回警告信息列表。不抛出 — 业务方决定是否阻断。
    策略：未知 key → 警告 + 保留（向后兼容）；类型/取值错误 → 警告 + 回退到默认值。
    """
    import logging

    log = logging.getLogger("spark.config")
    warns: list[str] = []
    defaults = _defaults()
    for key, value in list(cfg.items()):
        if key.startswith("_"):
            continue
        if key not in _SCHEMA:
            warns.append(f"未知配置项 '{key}'（{source}），已忽略。请检查拼写或升级版本。")
            continue
        expected, valid_set = _SCHEMA[key]
        if not isinstance(value, expected):
            coerced: int | float | bool | str | None = None
            # 轻微类型偏差尝试强转（int↔float / str↔bool）
            try:
                if expected is int:
                    coerced = int(value)
                elif expected is float:
                    coerced = float(value)
                elif expected is bool and isinstance(value, str):
                    coerced = value.lower() in ("1", "true", "yes", "on")
                elif expected is str:
                    coerced = str(value)
            except (ValueError, TypeError):
                pass
            if coerced is not None:
                log.warning("配置项 ‘%s’ 类型不匹配：期望 %s、实际 %s，已强转为 %s", key, expected, type(value).__name__, coerced)
                cfg[key] = coerced
            else:
                log.warning("配置项 ‘%s’ 类型错误：期望 %s、实际 %s，已回退为默认值", key, expected, type(value).__name__)
                cfg[key] = defaults[key]
                warns.append(f"配置项 ‘{key}’ 类型错误，已使用默认值")
            continue
        if valid_set is not None and value not in valid_set:
            log.warning("配置项 ‘%s’ 取值 ‘%s’ 不在合法集合 %s 中，已回退为默认值", key, value, valid_set)
            cfg[key] = defaults[key]
            warns.append(f"配置项 ‘{key}’ 取值 ‘{value}’ 非法，已使用默认值")
    return warns


def load_config() -> dict:
    cfg = _defaults()
    f = config_file()
    if f.exists():
        try:
            data = tomlkit.parse(f.read_text(encoding="utf-8"))
            for k in cfg:
                if k in data:
                    cfg[k] = data[k]
            # 收集 tomlkit 阶段未报出的未知 key（用于 schema 校验）
            unknown_keys = {k for k in data if k not in cfg}
            # tomlkit 的 Table/List 转成普通 dict/list（数组的表 → [[mcp_servers]] 等）
            for k in ("mcp_servers", "protected_paths"):
                if isinstance(cfg.get(k), list):
                    cfg[k] = json.loads(json.dumps(cfg[k]))
            if isinstance(cfg.get("usage_pricing"), dict):
                cfg["usage_pricing"] = json.loads(json.dumps(cfg["usage_pricing"]))
            # Schema 校验：类型 / 取值 / 未知 key
            if unknown_keys:
                for k in unknown_keys:
                    cfg[k] = data[k]  # 暂存（validate 会识别并警告未知）
            warns = _validate_and_coerce(cfg, source=str(f))
            if warns:
                cfg["_config_warnings"] = warns
        except PermissionError as exc:
            # 权限不足时不静默回退 — 明确告知，避免"看起来启动了但读不到配置"
            import logging
            logging.getLogger("spark.config").error(
                "配置文件权限不足（%s），使用默认配置。请检查：%s", exc, f
            )
        except Exception:
            # 配置损坏时退回默认，并把坏文件改名留档，不覆盖用户数据。
            backup = f.with_suffix(".toml.bak")
            try:
                os.replace(f, backup)
            except OSError:
                pass
    # 访问令牌默认空 = 免登录。默认监听 0.0.0.0（便于外部预览/代理访问），
    # 公网可达时务必在设置里开启令牌；仅本机使用可显式 `--host 127.0.0.1`。
    # 用户可在设置里显式开启；开启后所有请求必须携带。
    cfg["token"] = str(cfg.get("token") or "").strip()
    # API Key 环境变量回退（对标主流 agent：CLAUDE_API_KEY / OPENAI_API_KEY 等）：
    # 配置里没填密钥时，依次读 SPARK_API_KEY → <provider>_API_KEY。
    # 来自环境变量的密钥打运行时标记，保存设置时不会被写盘固化。
    if not str(cfg.get("api_key") or "").strip():
        env_key = os.environ.get("SPARK_API_KEY") or ""
        if not env_key:
            env_key = os.environ.get(_PROVIDER_ENV.get(str(cfg.get("provider") or ""), "")) or ""
        if env_key:
            cfg["api_key"] = env_key.strip()
            cfg["_env_api_key"] = True
    return cfg


def save_config(cfg: dict) -> None:
    f = config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    for k, v in cfg.items():
        # 仅持久化 TOML 基础类型与白名单容器（数组的表 / 表）；
        # 运行时字段（如 _config_warnings、_env_api_key 标记）与未知 key 不写盘。
        if k.startswith("_"):
            continue
        if k not in _SCHEMA and k != "mock_script":
            continue
        if v is None:
            continue
        if isinstance(v, dict):
            if k == "usage_pricing" and v:
                doc[k] = v
            continue
        if isinstance(v, list):
            if k in ("mcp_servers", "protected_paths") and v:
                doc[k] = v
            continue
        doc[k] = v
    f.write_text(tomlkit.dumps(doc), encoding="utf-8")
    _harden_permissions(f)


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
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            m = re.search(r"S-1-\d[\d-]*\d", out.stdout or "")
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


def mask_key(key: str) -> str:
    """API Key 打码显示：sk-1234567890 → sk-1********7890。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * 6
    return key[:4] + "*" * 8 + key[-4:]


def is_masked_key(key: str) -> bool:
    """判定是否为打码后的密钥（前端回显值）：8 连星或全星号即视为打码，禁止写回真值。"""
    return "*" * 8 in key or set(key) <= {"*"}


def apply_preset(cfg: dict, preset: str) -> dict:
    """把某预设的 base_url/model 应用到配置（api_key 保留当前值）。"""
    p = PRESETS.get(preset)
    if not p:
        return cfg
    cfg["provider"] = preset
    cfg["base_url"] = p["base_url"]
    cfg["model"] = p["model"]
    if preset == "mock":
        cfg["api_key"] = ""
    return cfg


# ---------------------------------------------------------------------------
# 类型化 Provider 配置（消除 _build_provider_cfg 手动拼字段的脆弱性）
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """stream_chat / AgentLoop 的 provider 参数类型。

    替代裸 dict：_old_build_provider_cfg 从 state.cfg 手动挑 9 个字段，
    拼错字段名/漏传只在运行时暴露。ProviderConfig 字段带类型提示，
    IDE 与 ruff 都能提前发现问题。
    """
    provider: str = "mock"
    base_url: str = ""
    model: str = "mock"
    api_key: str = ""
    model_fast: str = ""
    fallback_model: str = ""
    temperature: str | float = ""
    max_tokens: str | int = ""
    route_enabled: bool = True
    route_keywords: str = ""
    # 演示/测试脚本（不进配置文件）
    mock_script: list | None = None
    mock_subagent_script: list | None = None

    @classmethod
    def from_cfg(cls, cfg: dict, model_override: str = "") -> ProviderConfig:
        """从完整配置构造 ProviderConfig（集中字段挑选逻辑）。"""
        return cls(
            provider=cfg.get("provider", "mock"),
            base_url=cfg.get("base_url", ""),
            model=model_override or cfg.get("model", "mock"),
            api_key=cfg.get("api_key", ""),
            model_fast=cfg.get("model_fast", ""),
            fallback_model=cfg.get("fallback_model", ""),
            temperature=cfg.get("temperature", ""),
            max_tokens=cfg.get("max_tokens", ""),
            route_enabled=cfg.get("route_enabled", True),
            route_keywords=cfg.get("route_keywords", ""),
            mock_script=cfg.get("mock_script"),
            mock_subagent_script=cfg.get("mock_subagent_script"),
        )

    def to_dict(self) -> dict:
        """下游 provider.py / compaction.py 仍吃 dict——提供显式转换。"""
        d = {k: v for k, v in self.__dict__.items() if v is not None}
        return d
