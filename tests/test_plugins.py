"""插件加载测试（P3 ⑥）：正常插件、失败插件隔离、collect 汇总。"""
from __future__ import annotations

from pathlib import Path

from spark2.plugins import collect_plugin_tools, load_plugins
from spark2.tools.base import Tool


def _write_plugin(d: Path, name: str, code: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.py"
    p.write_text(code, encoding="utf-8")
    return p


def test_load_plugin_tools_list(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path, "hello",
        "from spark2.tools.base import Tool\n"
        "def _h(args, ctx):\n    return 'hello '\n"
        "tools = [Tool(name='hello', description='say hello', parameters={'type':'object','properties':{}}, category='read', handler=_h)]\n",
    )
    loaded = load_plugins(tmp_path)
    assert "hello" in loaded and loaded["hello"]["error"] is None
    tools = collect_plugin_tools(tmp_path)
    assert len(tools) == 1 and tools[0].name == "hello"
    assert tools[0].plugin == "hello"


def test_load_plugin_register_function(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path, "reg",
        "from spark2.tools.base import Tool\n"
        "def register(reg):\n"
        "    reg.append(Tool(name='reg_tool', description='d', parameters={'type':'object','properties':{}}, category='read', handler=lambda a,c:'ok'))\n",
    )
    loaded = load_plugins(tmp_path)
    assert loaded["reg"]["error"] is None
    assert loaded["reg"]["tools"][0].name == "reg_tool"


def test_broken_plugin_isolated(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "good", "tools = []\n")
    _write_plugin(tmp_path, "bad", "raise RuntimeError('boom')\n")
    loaded = load_plugins(tmp_path)
    assert loaded["good"]["error"] is None
    assert loaded["bad"]["error"] and "RuntimeError" in loaded["bad"]["error"]
    # collect 只收正常插件
    tools = collect_plugin_tools(tmp_path)
    assert tools == []
