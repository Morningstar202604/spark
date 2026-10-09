"""子进程环境变量白名单——防止宿主 API 密钥随子进程外泄。

Agent 执行的 shell / PTY 命令会继承宿主进程的完整 ``os.environ``，
其中往往包含模型 API key、云凭证等敏感项。模型只需一步
``print(os.environ)`` + ``curl`` 就能把它们带出，``sanitize`` 脱敏层
只作用于 SSE 展示出口，对子进程内部的网络调用无能为力。

白名单只放行命令执行所必需的环境变量（shell、路径、区域、编码等），
明确拒收任何 *_KEY / *_TOKEN / *_SECRET / PASSWORD 形式的变量，
即使未来新增这些键也不会因「没 Listing 到」而被放行。
"""

from __future__ import annotations

import os
import re
from typing import Final

_ALLOWLIST_RE: Final = re.compile(
    r"""
    ^(
      PATH
      |HOME
      |USER|LOGNAME
      |SHELL
      |TERM|COLORTERM
      |LANG|LC_ALL|LC_CTYPE|LANGUAGE
      |TMPDIR|TEMP|TMP
      |EDITOR|PAGER
      |XDG_.*
      |PYTHON(PATH|IOENCODING|UNBUFFERED|NOUSERSITE|PIP_.*)
      |PIP_.*
      |NODE_.*|NPM_.*|NVM_.*
      |JAVA_HOME|ANDROID_HOME|MAVEN_.*|GRADLE_.*
      |HISTFILE|HISTSIZE
      |PS1|PROMPT  # 交互式 shell 的提示符（PTY）
      |WINDIR|SYSTEMROOT|SYSTEMDRIVE  # Windows 系统根
      |COMSPEC|CMDLOGIN
      |SSH_AUTH_SOCK|SSH_AGENT_LAUNCHER  # SSH agent 转发
      |DISPLAY|WAYLAND_DISPLAY|XAUTHORITY  # X/Wayland 显示
      |TERM_SESSION_ID|ITERM_PROFILE|ITERM_SESSION_ID  # iTerm 集成
      |CTERM_VERSION|TERM_PROGRAM  # 终端标识（只读）
      |COLORFGBG
      |OLDPWD|PWD
      |HOSTNAME
      |TZ
    )$
    """,
    re.VERBOSE | re.IGNORECASE,
)
_DENYLIST_RE: Final = re.compile(
    r".*(_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIAL|PIN|APIKEY)$", re.IGNORECASE
)


def is_env_allowed(key: str) -> bool:
    """判断环境变量键名是否属于子进程执行所必需。"""
    if not key or not isinstance(key, str):
        return False
    # 先匹配拒收：哪怕未来新增了 XXX_API_KEY 也在拒收范围
    if _DENYLIST_RE.match(key):
        return False
    return bool(_ALLOWLIST_RE.match(key))


def build_clean_env(
    base: dict[str, str] | None = None,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """构建子进程安全环境变量：白名单基础 + extra 覆盖。

    extra 中的键会被原样合并——调用方需对 extra 的内容负责；
    建议使用 build_clean_env({"LANG": "C.UTF-8"}) 指明区域覆盖。
    """
    src = base if base is not None else os.environ
    env: dict[str, str] = {k: v for k, v in src.items() if is_env_allowed(k)}
    if extra:
        env.update(extra)
    return env
