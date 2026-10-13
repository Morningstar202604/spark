"""P1-8 git execution memory: task-as-branch worktree isolation + auto-commit.

End-to-end against *real* temporary git repositories (no network):
create worktree -> step commits gated by tests -> failure cleanup -> main tree
stays pristine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from spark.git_memory import (
    TaskBeginArgs,
    TaskFinishArgs,
    TaskStepCommitArgs,
    slug_branch,
    task_begin,
    task_finish,
    task_step_commit,
)
from spark.sandbox import WorkdirSandbox


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def init_repo(root: Path, with_tests: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
    (root / "app.py").write_text("# base\n", encoding="utf-8")
    if with_tests:
        (root / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )
        (root / "tests").mkdir()
        (root / "tests" / "test_smoke.py").write_text(
            "def test_smoke():\n    assert True\n", encoding="utf-8"
        )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def sandbox(root: Path) -> WorkdirSandbox:
    return WorkdirSandbox(root)


def main_tip(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def main_clean(repo: Path) -> bool:
    return git(repo, "status", "--porcelain").stdout.strip() == ""


def main_tracked_clean(repo: Path) -> bool:
    """No tracked-file modifications; only untracked (``??``) entries allowed.

    A *kept* worktree lives inside the main checkout (the sandboxed worktree API
    requires it), so its registered directory shows as ``?? task/``. That entry
    is the worktree itself, not pollution of tracked files.
    """
    lines = git(repo, "status", "--porcelain").stdout.splitlines()
    return all(line.startswith("??") for line in lines)


# ---------- slug ----------


def test_slug_branch_safe() -> None:
    assert slug_branch("P1-8 git memory!") == "task/P1-8-git-memory"
    assert slug_branch("task/already") == "task/already"


# ---------- begin / commit loop / success finish ----------


def test_begin_stepcommit_loop_and_success_finish(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    sb = sandbox(repo)
    base_tip = main_tip(repo)

    begun = task_begin(sb, TaskBeginArgs(task="demo feature"))
    assert begun.ok, begun.payload
    branch = begun.payload["branch"]
    wt = Path(begun.payload["worktree_path"])
    assert branch == "task/demo-feature"
    assert wt.is_dir() and (wt / ".git").exists()

    # main tree untouched at begin
    assert (repo / "app.py").read_text(encoding="utf-8") == "# base\n"
    assert main_tip(repo) == base_tip

    # step 1: change file -> green tests -> auto-commit
    (wt / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
    stepped = task_step_commit(sb, TaskStepCommitArgs(worktree=str(wt), step="feat: add VALUE=42"))
    assert stepped.ok, stepped.payload
    assert stepped.payload["committed"] is True
    assert stepped.payload["test_gate"] == "passed"
    assert stepped.payload["commit"]
    # step subject lands in branch history
    log = git(wt, "log", "--oneline").stdout
    assert "add VALUE=42" in log

    # main tree STILL untouched (isolation)
    assert (repo / "app.py").read_text(encoding="utf-8") == "# base\n"
    assert main_tip(repo) == base_tip
    assert main_tracked_clean(repo)

    # step 2: no changes -> no-op
    noop = task_step_commit(sb, TaskStepCommitArgs(worktree=str(wt), step="nothing to do"))
    assert noop.ok and noop.payload["committed"] is False

    # success finish -> worktree kept, branch kept, merge hint returned
    done = task_finish(sb, TaskFinishArgs(worktree=str(wt), branch=branch, success=True))
    assert done.ok, done.payload
    assert done.payload["kept"] is True
    assert "merge" in done.payload["merge_hint"]
    assert wt.is_dir()
    assert branch in git(repo, "branch", "--list").stdout

    # main tree still pristine after a successful, kept task
    assert main_tip(repo) == base_tip
    assert main_tracked_clean(repo)


# ---------- test gate blocks a red step ----------


def test_red_step_is_not_committed(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    sb = sandbox(repo)
    begun = task_begin(sb, TaskBeginArgs(task="red step"))
    branch = begun.payload["branch"]
    wt = Path(begun.payload["worktree_path"])

    # break the smoke test -> tests go red
    (wt / "tests" / "test_smoke.py").write_text(
        "def test_smoke():\n    assert False\n", encoding="utf-8"
    )
    stepped = task_step_commit(sb, TaskStepCommitArgs(worktree=str(wt), step="should not land"))
    assert not stepped.ok
    assert stepped.payload["committed"] is False
    assert stepped.payload["test_gate"] == "failed"
    # the breaking edit is left uncommitted in the worktree, NOT on the branch
    assert "should not land" not in git(wt, "log", "--oneline").stdout
    assert git(wt, "status", "--porcelain").stdout.strip()

    # step-level immediate commit (run_tests=False) bypasses the gate
    forced = task_step_commit(
        sb,
        TaskStepCommitArgs(worktree=str(wt), step="checkpoint: red state", run_tests=False),
    )
    assert forced.ok and forced.payload["committed"] is True
    assert forced.payload["test_gate"] == "disabled"

    # cleanup the abandoned task
    fin = task_finish(sb, TaskFinishArgs(worktree=str(wt), branch=branch, success=False))
    assert fin.ok, fin.payload


# ---------- failure cleanup: worktree + branch gone, main tree pristine ----------


def test_failure_abandon_cleans_main_tree(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    sb = sandbox(repo)
    base_tip = main_tip(repo)

    begun = task_begin(sb, TaskBeginArgs(task="broken task"))
    branch = begun.payload["branch"]
    wt = Path(begun.payload["worktree_path"])

    # make a real commit on the task branch so there is something to discard
    (wt / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    s1 = task_step_commit(sb, TaskStepCommitArgs(worktree=str(wt), step="wip: first commit"))
    assert s1.ok and s1.payload["committed"] is True
    discarded_tip = s1.payload["commit"]

    # abandon
    fin = task_finish(sb, TaskFinishArgs(worktree=str(wt), branch=branch, success=False))
    assert fin.ok, fin.payload
    assert fin.payload["abandoned"] is True
    assert fin.payload["branch_deleted"] is True
    assert fin.payload["discarded_tip"] == discarded_tip

    # worktree directory removed, branch gone
    assert not wt.exists()
    assert branch not in git(repo, "branch", "--list").stdout

    # main tree completely clean and on its original commit
    assert main_tip(repo) == base_tip
    assert main_clean(repo)
    assert (repo / "app.py").read_text(encoding="utf-8") == "# base\n"


# ---------- no-test repo degrades gracefully ----------


def test_no_test_entrypoint_commits_without_gate(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo", with_tests=False)
    sb = sandbox(repo)
    begun = task_begin(sb, TaskBeginArgs(task="no tests"))
    wt = Path(begun.payload["worktree_path"])
    (wt / "app.py").write_text("x = 1\n", encoding="utf-8")
    stepped = task_step_commit(sb, TaskStepCommitArgs(worktree=str(wt), step="wip: no tests repo"))
    assert stepped.ok, stepped.payload
    assert stepped.payload["committed"] is True
    assert stepped.payload["test_gate"] == "not-detected"

    task_finish(
        sb,
        TaskFinishArgs(worktree=str(wt), branch=begun.payload["branch"], success=False),
    )
    assert main_clean(repo)
