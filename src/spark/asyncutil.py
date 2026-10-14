"""在同步上下文安全运行协程（含"事件循环已在运行"的降级路径）。

统一入口：web/state、memory/service、core/compact 过去各自内联同一套
workaround（thread + 新 loop），这里收敛为唯一实现。
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from typing import Any, Coroutine

DEFAULT_TIMEOUT = 120.0


def run_coroutine_sync(
    coro: Coroutine[Any, Any, Any], timeout: float | None = DEFAULT_TIMEOUT
) -> Any:
    """运行 *coro* 至完成并返回结果。

    当前线程无运行中的事件循环 → 直接 ``asyncio.run``；已有运行中的循环
    （从 async 代码经同步垫片回调）→ 另起线程 + 独立 loop，保留
    ``contextvars`` 上下文，避免 ``asyncio.run() cannot be called from a
    running event loop``。超时抛 :class:`TimeoutError`，协程内异常原样重抛。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[Any] = []
    error: list[BaseException] = []

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            ctx = contextvars.copy_context()
            result.append(ctx.run(loop.run_until_complete, coro))
        except BaseException as exc:  # noqa: BLE001 — 在调用线程原样重抛
            error.append(exc)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    thread = threading.Thread(target=_runner, daemon=True, name="spark-coro-sync")
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise TimeoutError(f"coroutine did not finish within {timeout}s")
    if error:
        raise error[0]
    return result[0] if result else None
