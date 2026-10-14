"""P0 安全特性：出口脱敏（sanitize）的行为锁。

覆盖三层脱敏（密钥/主目录/堆栈）+ SSE 出口（sanitize_sse_payload）。
根树参考实现见 git 历史 spark/sanitize.py。
"""

from __future__ import annotations

import pytest

from spark import sanitize as san


@pytest.fixture
def fake_home(monkeypatch: pytest.MonkeyPatch) -> str:
    home = "/home/testuser"
    # 适配重构后的 _HOME_CACHE (tuple[unix, fwd])；绕过 _get_home_prefixes() 惰性逻辑
    monkeypatch.setattr(san, "_HOME_CACHE", (home, home.replace("\\", "/")))
    return home


# ---------- sanitize(): 密钥 ----------


def test_sanitize_redacts_known_secret() -> None:
    secret = "sk-abcdef1234567890"
    out = san.sanitize(f"Authorization: Bearer {secret}", (secret,))
    assert secret not in out
    assert "[REDACTED]" in out


def test_sanitize_ignores_short_secrets() -> None:
    # 短密钥不做替换（按设计：>=8 字符才可能是真实凭据）
    out = san.sanitize("code is 12345", ("12345",))
    assert out == "code is 12345"


def test_sanitize_empty_and_none() -> None:
    assert san.sanitize("") == ""
    assert san.sanitize(None) == ""


# ---------- sanitize(): 主目录 ----------


def test_sanitize_hides_home_directory(fake_home: str) -> None:
    out = san.sanitize(f"file written to {fake_home}/project/main.py")
    assert fake_home not in out
    assert "~/project/main.py" in out


def test_sanitize_passthrough_ordinary_text(fake_home: str) -> None:
    text = "build finished in 42ms"
    assert san.sanitize(text) == text


# ---------- sanitize(): 堆栈折叠 ----------


def test_sanitize_collapses_traceback() -> None:
    text = (
        "before\n"
        'Traceback (most recent call last):\n'
        '  File "/x/y.py", line 3, in <module>\n'
        "    raise ValueError\n"
        "ValueError: bad input\n"
        "after"
    )
    out = san.sanitize(text)
    assert "Traceback" not in out
    assert "/x/y.py" not in out
    assert "[REDACTED] ValueError: bad input" in out
    assert "before" in out and "after" in out


# ---------- sanitize_sse_payload(): 浅递归脱敏 ----------


def test_sanitize_sse_payload_walks_nested_structures() -> None:
    secret = "sk-abcdef1234567890"
    payload = {
        "type": "tool",
        "data": {"output": f"token={secret}", "codes": [1, secret]},
    }
    out = san.sanitize_sse_payload(payload, (secret,))
    assert out["data"]["output"] == "token=[REDACTED]"
    assert out["data"]["codes"] == [1, "[REDACTED]"]


def test_sanitize_sse_payload_sanitizes_without_secrets(fake_home: str) -> None:
    # 线上无令牌/无 API Key 时主目录与堆栈依然必须脱敏
    payload = {
        "result": {
            "path": f"{fake_home}/.spark/store.db",
            "trace": "Traceback (most recent call last):\n boom: RuntimeError",
        }
    }
    out = san.sanitize_sse_payload(payload, ())
    assert fake_home not in out["result"]["path"]
    assert out["result"]["path"] == "~/.spark/store.db"
    assert "/x/y.py" not in out["result"]["trace"]
    assert out["result"]["trace"].startswith("[REDACTED]")


def test_sanitize_sse_payload_respects_depth_limit() -> None:
    secret = "sk-abcdef1234567890"
    payload = {"a": {"b": {"c": {"d": secret}}}}
    out = san.sanitize_sse_payload(payload, (secret,))
    # 深度限 3：第 4 层的字符串保持原样
    assert out["a"]["b"]["c"]["d"] == secret


def test_sanitize_sse_payload_keeps_non_strings() -> None:
    payload = {"ok": True, "code": 500, "items": [1, 2, None]}
    assert san.sanitize_sse_payload(payload, ("sk-abcdef1234567890",)) == payload
