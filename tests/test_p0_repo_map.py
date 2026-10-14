"""P0-10：tree-sitter repo-map 代码索引测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from spark.repo_map import RepoMapConfig, _bucket_rank, _iter_files, build_repo_map


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text(
        "def hello():\n    return 1\n\nclass Greeter:\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "app" / "utils.py").write_text(
        "def helper(x):\n    return x\n", encoding="utf-8"
    )
    (tmp_path / "app" / "README.md").write_text("# demo\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("let x = 1;\n", encoding="utf-8")
    return tmp_path


def test_bucket_rank():
    assert _bucket_rank(10) == 0
    assert _bucket_rank(100) == 1
    assert _bucket_rank(5000) == 2


def test_iter_files_excludes(tmp_path: Path):
    files = _iter_files(tmp_path, RepoMapConfig())
    assert all(".git" not in str(f) for f in files)


def test_build_repo_map_extracts_symbols(sample_repo: Path):
    repo_map = build_repo_map(sample_repo, RepoMapConfig())
    assert "Repo map" in repo_map
    # Python 符号应被 tree-sitter 提取
    assert "hello" in repo_map
    assert "Greeter" in repo_map
    assert "helper" in repo_map
    # node_modules 被排除
    assert "node_modules" not in repo_map
    # 非代码文件也进入地图（README）
    assert "README.md" in repo_map


def test_build_repo_map_disabled(sample_repo: Path):
    assert build_repo_map(sample_repo, RepoMapConfig(enabled=False)) == ""


def test_repo_map_in_system_prompt(sample_repo: Path, tmp_path: Path):
    """context.system_prompt 应注入 repo-map。"""
    from spark.config import SparkConfig
    from spark.core.context import system_prompt

    cfg = SparkConfig()
    prompt = system_prompt(sample_repo, cfg)
    assert "Repo map" in prompt
    assert "hello" in prompt
