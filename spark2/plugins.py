"""轻量插件点（P3 ⑥）：importlib 动态加载用户插件。

- 插件目录：~/.spark2/plugins/（或配置覆盖 plugins_dir），每个 .py 文件一个插件；
- 插件写法（两种皆可）：
    from spark2.tools import Tool
    def tools():
        return [Tool(name="my_tool", description="…", category="read", handler=handler)]
    或直接导出 tools 列表 / register(registry) 函数；
- 安全边界：插件是用户自己放在本地的代码，信任级与"内置终端/运行脚本"一致；
  插件注册的工具照常过审批门（category 决定审批与否），不绕过任何检查。
"""
from __future__ import annotations

import importlib.util
import traceback
from pathlib import Path
from typing import Any

from spark2.config import CONFIG_DIR


def plugins_dir() -> Path:
    return CONFIG_DIR / "plugins"


def load_plugins(base_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """加载全部插件，返回 {插件名: {tools, error?}}。单个插件失败不影响其他。"""
    root = base_dir or plugins_dir()
    root.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[str, Any]] = {}
    for f in sorted(root.glob("*.py")):
        name = f.stem
        if name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"spark2_plugins.{name}", f)
            if spec is None or spec.loader is None:
                result[name] = {"tools": [], "error": "无法加载模块"}
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            tools: list = []
            if hasattr(mod, "register") and callable(mod.register):
                reg: list = []
                mod.register(reg)
                tools = reg
            elif hasattr(mod, "tools"):
                t = mod.tools
                tools = t() if callable(t) else list(t)
            result[name] = {"tools": tools, "error": None}
        except Exception as e:  # noqa: BLE001
            result[name] = {"tools": [], "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
    return result


def collect_plugin_tools(base_dir: Path | None = None) -> list[Any]:
    """收集全部插件工具（含失败插件的错误说明工具？不——失败只记录，不注册）。"""
    tools: list[Any] = []
    loaded = load_plugins(base_dir)
    for name, info in loaded.items():
        if info.get("error"):
            continue
        for t in info["tools"]:
            t.plugin = name  # 标记来源
            tools.append(t)
    return tools
