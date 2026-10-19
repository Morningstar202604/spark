"""H3 三层权限作用域：deny 跨层优先 + 项目层仅 deny 的安全约束。"""

from __future__ import annotations

from pathlib import Path

from spark.config import SparkConfig, load_config
from spark.models import ToolCall
from spark.policy import decide


def _call(name: str = "run_shell", **args) -> ToolCall:
    return ToolCall(id="t", name=name, arguments=args or {"command": "ls"})


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load(workdir: Path, global_rules: str = "") -> SparkConfig:
    # 仅项目作用域文件参与本组测试（全局 config 缺省）
    return load_config(workdir=workdir)


def test_project_deny_blocks_even_with_local_allow(tmp_path: Path) -> None:
    _write(
        tmp_path / ".spark" / "settings.toml",
        '[[permission_rules]]\naction = "deny"\nresource = "secrets/**"\n',
    )
    _write(
        tmp_path / ".spark" / "settings.local.toml",
        '[[permission_rules]]\naction = "allow"\nresource = "**"\n',
    )
    cfg = _load(tmp_path)
    assert cfg.permission_scopes["project"], "project deny 规则应被采纳"
    tool = _call("write_file", path="secrets/key.txt")
    assert (
        decide(
            "full-auto",
            tool,
            allow_always=set(),
            readonly_mcp=set(),
            scopes=cfg.permission_scopes,
        )
        == "deny"
    )


def test_project_deny_beats_allow_always(tmp_path: Path) -> None:
    _write(
        tmp_path / ".spark" / "settings.toml",
        '[[permission_rules]]\naction = "deny"\nresource = "/etc/**"\n',
    )
    cfg = _load(tmp_path)
    from spark.policy import call_signature

    # write_file 的 resource 是目标路径；项目层 deny 覆盖 allow_always
    tool = _call("write_file", path="/etc/hostname")
    assert (
        decide(
            "suggest",
            tool,
            allow_always={call_signature(tool)},
            readonly_mcp=set(),
            scopes=cfg.permission_scopes,
        )
        == "deny"
    )


def test_local_allow_grants_when_no_deny(tmp_path: Path) -> None:
    _write(
        tmp_path / ".spark" / "settings.local.toml",
        '[[permission_rules]]\naction = "allow"\nresource = "src/**"\n',
    )
    cfg = _load(tmp_path)
    tool = _call("write_file", path="src/a.py")
    assert (
        decide(
            "suggest",
            tool,
            allow_always=set(),
            readonly_mcp=set(),
            scopes=cfg.permission_scopes,
        )
        == "allow"
    )
    other = _call("write_file", path="docs/b.md")
    assert (
        decide(
            "suggest",
            other,
            allow_always=set(),
            readonly_mcp=set(),
            scopes=cfg.permission_scopes,
        )
        == "prompt"
    )


def test_project_layer_strips_allow_rules_untrusted(tmp_path: Path) -> None:
    """仓库自带的 allow/prompt 规则一律剥离——只有 deny 能落进 project 层。"""
    _write(
        tmp_path / ".spark" / "settings.toml",
        (
            "[[permission_rules]]\n"
            'action = "allow"\n'
            'resource = "**"\n'
            "\n"
            "[[permission_rules]]\n"
            'action = "deny"\n'
            'resource = ".git/**"\n'
        ),
    )
    cfg = _load(tmp_path)
    assert [r.action for r in cfg.permission_scopes["project"]] == ["deny"]
    assert [r.resource for r in cfg.permission_scopes["project"]] == [".git/**"]


def test_scopes_empty_resource_never_matches(tmp_path: Path) -> None:
    _write(
        tmp_path / ".spark" / "settings.local.toml",
        '[[permission_rules]]\naction = "allow"\nresource = "**"\n',
    )
    cfg = _load(tmp_path)
    # task / web_fetch 等 resource 为空的工具：allow ** 不得放行
    tool = _call("task", prompt="x")
    assert (
        decide(
            "suggest",
            tool,
            allow_always=set(),
            readonly_mcp=set(),
            scopes=cfg.permission_scopes,
        )
        == "prompt"
    )


def test_flat_rules_still_work_without_scopes(tmp_path: Path) -> None:
    """旧扁平 rules 参数路径向后兼容（scopes=None）。"""
    from spark.config import PermissionRule

    rules = [PermissionRule(action="deny", resource="**/secret*")]
    tool = _call("write_file", path="config/secret.py")
    assert (
        decide(
            "full-auto",
            tool,
            allow_always=set(),
            readonly_mcp=set(),
            rules=rules,
        )
        == "deny"
    )
