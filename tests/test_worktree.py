from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel

from spark.sandbox import WorkdirSandbox
from spark.tools import gitops


class WorktreeListArgs(BaseModel):
    path: str = "."


class WorktreeCreateArgs(BaseModel):
    branch: str
    path: str = "."
    base: str | None = None


class WorktreeRemoveArgs(BaseModel):
    worktree: str
    force: bool = False


def init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )


def sandbox_for(root: Path) -> WorkdirSandbox:
    return WorkdirSandbox(root)


# ---------- validation ----------


def test_worktree_create_rejects_invalid_branch(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    result = gitops.worktree_create(
        sandbox_for(repo), WorktreeCreateArgs(branch="--upload-pack=evil")
    )
    assert not result.ok
    assert "branch" in str(result.payload.get("error", "")).lower()


def test_worktree_create_rejects_unsafe_base(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    result = gitops.worktree_create(
        sandbox_for(repo), WorktreeCreateArgs(branch="feature/x", base="--exec=evil")
    )
    assert not result.ok


def test_worktree_remove_rejects_path_traversal(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    result = gitops.worktree_remove(
        sandbox_for(repo), WorktreeRemoveArgs(worktree="../../etc")
    )
    assert not result.ok


# ---------- real worktree behaviour ----------


def test_worktree_create_and_list(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    sb = sandbox_for(repo)
    created = gitops.worktree_create(sb, WorktreeCreateArgs(branch="feature/one"))
    assert created.ok, created.payload
    listed = gitops.worktree_list(sb, WorktreeListArgs())
    assert listed.ok
    output = str(listed.payload.get("output", ""))
    assert "feature/one" in output


def test_worktree_isolates_changes_from_main_tree(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    sb = sandbox_for(repo)
    created = gitops.worktree_create(sb, WorktreeCreateArgs(branch="feature/iso"))
    assert created.ok, created.payload
    target = Path(str(created.payload["worktree_path"]))
    (target / "a.txt").write_text("changed\n", encoding="utf-8")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"


def test_worktree_remove_delegistry(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    sb = sandbox_for(repo)
    created = gitops.worktree_create(sb, WorktreeCreateArgs(branch="feature/tmp"))
    assert created.ok
    target = str(created.payload["worktree_path"])
    removed = gitops.worktree_remove(
        sb, WorktreeRemoveArgs(worktree=target, force=True)
    )
    assert removed.ok, removed.payload
    assert not Path(target).exists()


def test_worktree_requires_git_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    result = gitops.worktree_list(sandbox_for(plain), WorktreeListArgs())
    assert not result.ok


# ---------- auto test loop ----------


def test_detect_test_command_prefers_pytest(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    assert "pytest" in (gitops.detect_test_command(tmp_path) or "")


def test_detect_test_command_falls_back_to_npm(tmp_path: Path) -> None:
    import json

    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )
    assert "npm" in gitops.detect_test_command(tmp_path)


def test_detect_test_command_returns_none_without_signal(tmp_path: Path) -> None:
    assert gitops.detect_test_command(tmp_path) is None
