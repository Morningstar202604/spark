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
import secrets
from pathlib import Path

import tomlkit

def config_dir() -> Path:
    """配置目录：默认 ~/.spark2；可用环境变量 SPARK2_HOME 覆盖（测试隔离用）。"""
    return Path(os.environ.get("SPARK2_HOME") or (Path.home() / ".spark2"))


def config_file() -> Path:
    return config_dir() / "config.toml"


# 兼容引用（动态读取，勿在模块导入时固化路径）
CONFIG_FILE = config_file()
CONFIG_DIR = config_dir()

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
    "mock": {
        "label": "演示模式（无需密钥）",
        "base_url": "",
        "model": "mock",
        "api_key": "",
    },
}

APPROVAL_MODES = ("suggest", "auto-edit", "full-auto")


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
        # 语义记忆：off=仅关键词检索（默认，零依赖）/ api=火山方舟 doubao-embedding / local=本地模型
        "memory_embedding": "off",
        "memory_embed_model": "",
        "mcp_servers": [],
    }


def load_config() -> dict:
    cfg = _defaults()
    f = config_file()
    if f.exists():
        try:
            data = tomlkit.parse(f.read_text(encoding="utf-8"))
            for k in cfg:
                if k in data:
                    cfg[k] = data[k]
            # tomlkit 的 Table/List 转成普通 dict/list（数组的表 → [[mcp_servers]]）
            if isinstance(cfg.get("mcp_servers"), list):
                cfg["mcp_servers"] = json.loads(json.dumps(cfg["mcp_servers"]))
        except Exception:
            # 配置损坏时退回默认，并把坏文件改名留档，不覆盖用户数据。
            backup = f.with_suffix(".toml.bak")
            try:
                os.replace(f, backup)
            except OSError:
                pass
    _ensure_token(cfg)
    return cfg


def _ensure_token(cfg: dict) -> None:
    if not cfg.get("token"):
        cfg["token"] = secrets.token_hex(16)
        save_config(cfg)


def save_config(cfg: dict) -> None:
    f = config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    doc = tomlkit.document()
    for k, v in cfg.items():
        # 仅持久化 TOML 基础类型与 mcp_servers（数组的表）；运行时字段（如 mock_script）不写盘
        if v is None or isinstance(v, dict):
            continue
        if isinstance(v, list) and k != "mcp_servers":
            continue
        if k == "mcp_servers" and not v:
            continue
        doc[k] = v
    f.write_text(tomlkit.dumps(doc), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def mask_key(key: str) -> str:
    """API Key 打码显示：sk-1234567890 → sk-1********7890。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * 6
    return key[:4] + "*" * 8 + key[-4:]


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
