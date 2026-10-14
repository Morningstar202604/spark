"""跨平台兼容常量（Windows 专属 subprocess 标志的安全取值）。

``subprocess.CREATE_NO_WINDOW`` / ``CREATE_NEW_PROCESS_GROUP`` / ``DETACHED_PROCESS``
只在 Windows 的 ``subprocess`` 模块上存在；在 Linux/macOS 上直接访问会
``AttributeError``，类型检查器（mypy 按目标平台解析 typeshed）也会报
``Module has no attribute``。统一用 ``getattr`` 取值并在非 Windows 上回退为 0
（0 是 ``creationflags`` 的合法"无标志"取值），使调用点在所有平台都安全。
"""

from __future__ import annotations

import subprocess

#: 不弹出控制台窗口。非 Windows 为 0（无副作用）。
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: 让子进程独立成组，便于整组终止。非 Windows 为 0（POSIX 侧用 start_new_session）。
CREATE_NEW_PROCESS_GROUP: int = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

#: 完全脱离父进程控制台。非 Windows 为 0。
DETACHED_PROCESS: int = getattr(subprocess, "DETACHED_PROCESS", 0)

__all__ = ["CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"]
