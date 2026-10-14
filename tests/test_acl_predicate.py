"""验证 ACL 判定在 GitHub runner 场景下正确（本机场景无法直接复现 runner ACL）。"""

from __future__ import annotations

from spark.config import _acl_wide_entries, win_acl_restricted

#: GitHub Actions runner 上 icacls 的真实输出形态（4 条系统条目，无普通用户组）
RUNNER_ACES = [
    r"C:\Users\runneradmin\AppData\Local\Temp\pytest-0\config.toml runneradmin:(F)",
    r"                                                     NT AUTHORITY\SYSTEM:(F)",
    r"                                                     BUILTIN\Administrators:(F)",
    r"                                                     OWNER RIGHTS:(F)",
]

#: 未收紧时的危险形态（BUILTIN\Users 可读）
INHERITED_ACES = [
    r"C:\Users\someone\config.toml BUILTIN\Users:(I)(RX)",
    r"                              NT AUTHORITY\SYSTEM:(I)(F)",
]


def test_runner_style_acl_counts_as_restricted() -> None:
    """runner 的 4 条系统条目不应被判为"未收紧"（旧实现 len==1 会误判）。"""
    assert _acl_wide_entries(RUNNER_ACES) == []
    assert len(RUNNER_ACES) == 4  # 旧断言 len(aces)==1 在此必然失败


def test_wide_user_group_is_rejected() -> None:
    """含 BUILTIN\\Users / Everyone / Authenticated Users 时必须判为未收紧。"""
    wide = _acl_wide_entries(INHERITED_ACES)
    assert wide == [INHERITED_ACES[0]]
    assert _acl_wide_entries([r"x Everyone:(F)"]) == [r"x Everyone:(F)"]
    assert _acl_wide_entries([r"x NT AUTHORITY\Authenticated Users:(M)"]) != []


def test_machine_and_admin_entries_are_not_wide() -> None:
    """系统/管理员/所有者条目不属于"宽泛可见"。"""
    not_wide = [
        r"x NT AUTHORITY\SYSTEM:(F)",
        r"x BUILTIN\Administrators:(F)",
        r"x CREATOR OWNER:(OI)(CI)(IO)(F)",
        r"x OWNER RIGHTS:(F)",
    ]
    assert _acl_wide_entries(not_wide) == []


def test_non_windows_returns_false(monkeypatch) -> None:
    from pathlib import Path

    import spark.config as cfg

    monkeypatch.setattr(cfg.sys, "platform", "linux")
    assert win_acl_restricted(Path("nonexistent")) is False
