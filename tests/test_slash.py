"""斜杠命令：解析、展开与前端清单端点。

回归背景：slash 匹配与展开此前各自 split 一遍字符串，展开侧解析出的 user_input
没有任何测试锁定；`/api/slash-commands` 同样无契约测试。此文件补齐最小闭环。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from spark.slash import SLASH_COMMANDS, expand_slash, list_commands, match_slash
from spark.store import SessionStore
from spark.web.server import AppState, create_app


def test_match_known_and_unknown() -> None:
    assert match_slash("/explain") is not None
    assert match_slash("/explain 这段") is not None
    assert match_slash("  /test  ") is not None
    assert match_slash("/nope") is None
    assert match_slash("普通问题，不用斜杠") is None
    assert match_slash("") is None


def test_match_is_case_insensitive() -> None:
    assert match_slash("/EXPLAIN") is not None


def test_expand_carries_user_input() -> None:
    out = expand_slash("/fix 登录页 500", workdir="/proj/app")
    assert out != "/fix 登录页 500"  # 已展开为模板
    assert "登录页 500" in out


def test_expand_passes_through_when_unmatched() -> None:
    assert expand_slash("/notacommand x", workdir="/w") == "/notacommand x"
    assert expand_slash("普通消息 /fix 混在里面", workdir="/w") == "普通消息 /fix 混在里面"


def test_expand_without_arguments_still_templates() -> None:
    out = expand_slash("/explain", workdir="/w")
    assert out != "/explain"
    assert "解释" in out


def test_every_command_expands_without_crashing() -> None:
    """每个预设指令都要能展开（漏写占位符、花括号写坏都会在这里暴露）。

    注意：当前 9 条模板都只用 {user_input}，没有一个用到 {workdir}——
    展开结果里不应残留未替换的占位符，workdir 参数保留供后续模板使用。
    """
    for cmd in SLASH_COMMANDS:
        raw = f"/{cmd.name} 目标"
        out = expand_slash(raw, workdir="/tmp/w")
        assert out != raw, f"{cmd.name} 未展开"
        assert "{user_input}" not in out and "{workdir}" not in out, f"{cmd.name} 占位符未替换"
        assert "目标" in out, f"{cmd.name} 丢失用户附加输入"


def test_list_commands_shape() -> None:
    items = list_commands()
    assert len(items) == len(SLASH_COMMANDS)
    for it in items:
        assert {"name", "label", "description"} <= set(it), it


@pytest.fixture()
def client(tmp_path: Path):
    state = AppState(
        cfg={
            "provider": "mock",
            "base_url": "",
            "model": "mock",
            "api_key": "",
            "workdir": str(tmp_path),
            "approval_mode": "suggest",
            "max_context_tokens": 32000,
            "token": "",
            "mock_script": None,
        },
        store=SessionStore(root=tmp_path / "sessions"),
    )
    with TestClient(create_app(state)) as c:
        yield c


def test_slash_commands_endpoint(client: TestClient) -> None:
    r = client.get("/api/slash-commands")
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["commands"]]
    assert "explain" in names and "fix" in names
