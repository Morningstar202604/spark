"""Spark Agent 重构版（spark）。

路线二：按《Spark Agent 体检与重构方案》第 4 节蓝图重写。
原则：单一 asyncio 事件循环；审批门而非假沙箱；成熟组件而非手写轮子；
单一前端随包发布；中文优先；不引入任何按轮计费的隐性 LLM 调用。
"""

def _resolve_version() -> str:
    """版本号单一来源：打包元数据（pyproject.toml）。

    直接跑源码或用 PyInstaller 打包时可能查不到 distribution 元数据，
    此时回落到下面的字面量——改动版本只需要动 pyproject.toml。
    """
    try:
        from importlib.metadata import version

        return version("spark-agent")
    except Exception:  # noqa: BLE001 - 元数据缺失不应影响导入
        return _FALLBACK_VERSION


_FALLBACK_VERSION = "0.9.0"

__version__ = _resolve_version()

