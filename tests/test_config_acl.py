"""配置文件权限收紧：POSIX chmod 600；Windows 用 ACL 只保留当前用户。

Windows 上 os.chmod(0o600) 只拨只读位、不限制其他用户读取——令牌与 API Key
存于 config.toml，必须用 icacls 收紧 ACL 才算真正保护。
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL 行为")
def test_save_config_hardens_acl(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.config import _defaults, config_file, save_config

    cfg = _defaults()
    cfg["api_key"] = "sk-test-1234567890"
    save_config(cfg)
    f = config_file()
    out = subprocess.run(
        ["icacls", str(f)], capture_output=True, text=True, timeout=30
    ).stdout
    assert "BUILTIN\\Users" not in out
    assert "Everyone" not in out
    assert "NT AUTHORITY\\Authenticated Users" not in out
    # 严格断言：继承已移除，DACL 只剩当前用户一条 ACE
    who = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    import re

    m = re.search(r"S-1-\d[\d-]*\d", who)
    assert m, f"whoami 未返回 SID：{who!r}"
    aces = [ln for ln in out.splitlines() if ":(" in ln]
    assert len(aces) == 1, f"应只剩 1 条 ACE，实际：{out!r}"
    assert m.group(0) in out or _user_name() in aces[0]


def _user_name() -> str:
    import getpass

    return getpass.getuser()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL 行为")
def test_doctor_perm_check_on_windows(tmp_path, monkeypatch) -> None:
    """doctor 的权限自检在 Windows 上应反映 ACL 状态（收紧后为 True）。"""
    monkeypatch.setenv("SPARK_HOME", str(tmp_path))
    from spark.cli import _perm_ok
    from spark.config import _defaults, save_config

    cfg = _defaults()
    save_config(cfg)
    assert _perm_ok() is True
