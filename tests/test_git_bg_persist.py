import time
from pathlib import Path

import pytest

from spark.config import AgentConfig, SparkConfig
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools import bg, gitops
from spark.tools.registry import ToolContext, ToolRegistry


def _cfg(mode: str = "workspace") -> SparkConfig:
    return SparkConfig(agent=AgentConfig(sandbox_mode=mode))  # type: ignore[arg-type]


def _repo(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "first"], cwd=repo, check=True)
    return repo


# ---- git read-only tools ----

def test_git_status_diff_log_branch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    sb = WorkdirSandbox(tmp_path, _cfg())
    st = gitops.git_status(sb, gitops.GitStatusArgs(path="repo"))
    assert st.ok, st.payload
    assert "main" in str(st.payload.get("output", "")) or "master" in str(st.payload.get("output", ""))

    (repo / "a.txt").write_text("changed\n")
    d = gitops.git_diff(sb, gitops.GitDiffArgs(path="repo"))
    assert d.ok and "changed" in str(d.payload.get("output", ""))

    lg = gitops.git_log(sb, gitops.GitLogArgs(path="repo", max_count=5))
    assert lg.ok and "first" in str(lg.payload.get("output", ""))

    br = gitops.git_branch(sb, gitops.GitBranchArgs(path="repo"))
    assert br.ok and br.payload["output"] in {"main", "master"}


def test_git_rejects_outside_workdir(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg())
    r = gitops.git_status(sb, gitops.GitStatusArgs(path="../elsewhere"))
    assert not r.ok
    assert "error" in r.payload


def test_git_rejects_non_repo(tmp_path: Path) -> None:
    (tmp_path / "plain").mkdir()
    sb = WorkdirSandbox(tmp_path, _cfg())
    r = gitops.git_status(sb, gitops.GitStatusArgs(path="plain"))
    assert not r.ok
    assert "not a git repository" in r.payload["error"]


def test_git_add_and_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "b.txt").write_text("new file\n")
    sb = WorkdirSandbox(tmp_path, _cfg())

    add = gitops.git_add(sb, gitops.GitAddArgs(paths=["b.txt"], path="repo"))
    assert add.ok, add.payload

    cmt = gitops.git_commit(sb, gitops.GitCommitArgs(message="add b", path="repo"))
    assert cmt.ok, cmt.payload

    lg = gitops.git_log(sb, gitops.GitLogArgs(path="repo", max_count=1))
    assert "add b" in str(lg.payload.get("output", ""))


def test_git_commit_add_all(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "c.txt").write_text("x\n")
    sb = WorkdirSandbox(tmp_path, _cfg())
    cmt = gitops.git_commit(sb, gitops.GitCommitArgs(message="all", path="repo", add_all=True))
    assert cmt.ok, cmt.payload
    lg = gitops.git_log(sb, gitops.GitLogArgs(path="repo", max_count=1))
    assert "all" in str(lg.payload.get("output", ""))


def test_git_commit_requires_message(tmp_path: Path) -> None:
    _repo(tmp_path)
    sb = WorkdirSandbox(tmp_path, _cfg())
    r = gitops.git_commit(sb, gitops.GitCommitArgs(message="  ", path="repo"))
    assert not r.ok
    assert "message required" in r.payload["error"]


def test_git_add_rejects_escaping_path(tmp_path: Path) -> None:
    _repo(tmp_path)
    sb = WorkdirSandbox(tmp_path, _cfg())
    r = gitops.git_add(sb, gitops.GitAddArgs(paths=["../outside.txt"], path="repo"))
    assert not r.ok
    assert "rejected" in r.payload["error"]


def test_registry_has_git_tools(tmp_path: Path) -> None:
    ctx = ToolContext(sandbox=WorkdirSandbox(tmp_path, _cfg()), config=_cfg())
    reg = ToolRegistry(ctx)
    names = {s["function"]["name"] for s in reg.schemas()}
    assert {"git_status", "git_diff", "git_log", "git_branch", "git_add", "git_commit"} <= names


# ---- bg persistence ----

@pytest.fixture(autouse=True)
def _detach_store():
    yield
    # wait for daemon reader threads of this test's jobs to finish persisting
    deadline = time.time() + 5
    while time.time() < deadline:
        with bg.JOBS_LOCK:
            pending = [j for j in bg.JOBS.values() if j["finished_at"] is not None and j["exit_code"] is None]
        if not pending:
            break
        time.sleep(0.05)
    bg.attach_store(None)


def test_bg_persist_roundtrip(tmp_path: Path) -> None:
    store = SessionStore(":memory:")
    bg.attach_store(store)
    sandbox = WorkdirSandbox(tmp_path, _cfg())
    r = bg.bg_start_tool(sandbox, {"command": "echo persisted"})
    assert r.ok, r.payload
    job_id = r.payload["job_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        out = bg.bg_output_tool({"job_id": job_id})
        if "persisted" in str(out.payload.get("output", "")):
            break
        time.sleep(0.1)
    assert "persisted" in str(out.payload.get("output", ""))
    row = store.get_bg_job(job_id)
    assert row is not None and "persisted" in row["output"]

    listing = bg.bg_list_tool()
    ids = {j["job_id"] for j in listing.payload["jobs"]}
    assert job_id in ids
    store.close()


def test_bg_output_falls_back_to_store_after_restart(tmp_path: Path) -> None:
    store = SessionStore(":memory:")
    bg.attach_store(store)
    sandbox = WorkdirSandbox(tmp_path, _cfg())
    r = bg.bg_start_tool(sandbox, {"command": "echo survived"})
    job_id = r.payload["job_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        out = bg.bg_output_tool({"job_id": job_id})
        if "survived" in str(out.payload.get("output", "")):
            break
        time.sleep(0.1)
    bg.JOBS.clear()  # simulate a server restart losing in-memory state
    after = bg.bg_output_tool({"job_id": job_id})
    assert after.ok, after.payload
    assert after.payload.get("lost") is True
    assert "survived" in str(after.payload.get("output", ""))
    store.close()


def test_bg_list_shows_lost_jobs(tmp_path: Path) -> None:
    store = SessionStore(":memory:")
    bg.attach_store(store)
    sandbox = WorkdirSandbox(tmp_path, _cfg())
    r = bg.bg_start_tool(sandbox, {"command": "echo lost-list"})
    job_id = r.payload["job_id"]
    bg.JOBS.clear()  # simulate restart
    listing = bg.bg_list_tool()
    entry = next((j for j in listing.payload["jobs"] if j["job_id"] == job_id), None)
    assert entry is not None
    assert entry["lost"] is True
    assert entry["running"] is False
    store.close()


def test_bg_list_without_store_still_works(tmp_path: Path) -> None:
    bg.attach_store(None)
    sandbox = WorkdirSandbox(tmp_path, _cfg())
    r = bg.bg_start_tool(sandbox, {"command": "echo plain"})
    assert r.ok
    listing = bg.bg_list_tool()
    assert any(j["job_id"] == r.payload["job_id"] for j in listing.payload["jobs"])
