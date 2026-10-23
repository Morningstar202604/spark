"""技能 / 提示词 / 插件 / 知识库 四组 POST 端点。

重点在两条：写操作不能逃出 `.spark/skills` 与 `.spark/commands`；读操作不能
用 `..` 或绝对路径读到工作区外。
"""

from __future__ import annotations

import json
from pathlib import Path

from spark.web._handlers_content import (
    _command_entries,
    _plugin_entries,
    _safe_rel,
    _skill_entries,
)


class TestSafeRel:
    def test_normal(self) -> None:
        assert _safe_rel("a/b.md") == "a/b.md"

    def test_backslashes_normalised(self) -> None:
        assert _safe_rel("a\\b.md") == "a/b.md"

    def test_dot_segments_dropped(self) -> None:
        assert _safe_rel("./a/./b.md") == "a/b.md"

    def test_rejects_parent(self) -> None:
        assert _safe_rel("../secret") is None
        assert _safe_rel("a/../../x") is None

    def test_rejects_absolute(self) -> None:
        assert _safe_rel("/etc/passwd") is None

    def test_rejects_drive_letter(self) -> None:
        assert _safe_rel("C:/Windows/x") is None
        assert _safe_rel("c:\\Windows\\x") is None

    def test_rejects_empty(self) -> None:
        assert _safe_rel("") is None
        assert _safe_rel("   ") is None
        assert _safe_rel(".") is None


class TestSkillEntries:
    def test_empty_workdir(self, tmp_path: Path) -> None:
        assert _skill_entries(tmp_path) == []

    def test_discovers_skill(self, tmp_path: Path) -> None:
        d = tmp_path / ".spark" / "skills"
        d.mkdir(parents=True)
        (d / "review.md").write_text(
            "---\nname: review\ndescription: 审查代码\n---\n\n正文",
            encoding="utf-8",
        )
        items = _skill_entries(tmp_path)
        assert len(items) == 1
        assert items[0]["name"] == "review"
        assert items[0]["path"] == ".spark/skills/review.md"


class TestCommandEntries:
    def test_empty(self, tmp_path: Path) -> None:
        assert _command_entries(tmp_path) == []

    def test_discovers_command(self, tmp_path: Path) -> None:
        d = tmp_path / ".spark" / "commands"
        d.mkdir(parents=True)
        (d / "fix.md").write_text("# 修复\n\n按这个流程修 bug", encoding="utf-8")
        items = _command_entries(tmp_path)
        assert [c["name"] for c in items] == ["fix"]


class TestPluginEntries:
    def test_no_dir(self, tmp_path: Path) -> None:
        assert _plugin_entries(tmp_path) == []

    def test_plugin_with_manifest(self, tmp_path: Path) -> None:
        d = tmp_path / ".spark" / "plugins" / "demo"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text(
            json.dumps({"name": "demo-plugin", "description": "示例", "entry": "run.py"}),
            encoding="utf-8",
        )
        items = _plugin_entries(tmp_path)
        assert len(items) == 1
        assert items[0]["name"] == "demo-plugin"
        assert items[0]["has_manifest"] is True

    def test_plugin_without_manifest(self, tmp_path: Path) -> None:
        d = tmp_path / ".spark" / "plugins" / "bare"
        d.mkdir(parents=True)
        items = _plugin_entries(tmp_path)
        assert items[0]["name"] == "bare"
        assert items[0]["has_manifest"] is False

    def test_broken_manifest_does_not_raise(self, tmp_path: Path) -> None:
        d = tmp_path / ".spark" / "plugins" / "broken"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text("{oops", encoding="utf-8")
        items = _plugin_entries(tmp_path)
        assert len(items) == 1
        assert items[0]["has_manifest"] is True

    def test_hidden_dirs_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".spark" / "plugins" / ".git").mkdir(parents=True)
        assert _plugin_entries(tmp_path) == []
