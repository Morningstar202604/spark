from pathlib import Path

import pytest

from spark.config import AgentConfig, SparkConfig
from spark.errors import PathEscapeError
from spark.sandbox import SandboxPolicyError, WorkdirSandbox
from spark.tools.shell import RunShellArgs, run_shell


def _cfg(mode: str, protected: list[str] | None = None) -> SparkConfig:
    agent = AgentConfig(sandbox_mode=mode)  # type: ignore[arg-type]
    if protected:
        agent.protected_paths = protected
    return SparkConfig(agent=agent)


def test_workspace_mode_blocks_outside_paths(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    with pytest.raises(PathEscapeError):
        sb.resolve("/etc/passwd")


def test_full_access_allows_outside_but_protects(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("full-access"))
    outside = tmp_path.parent / "outside-ok"
    assert sb.resolve(str(outside)) == outside.resolve()
    sb2 = WorkdirSandbox(tmp_path, _cfg("full-access", protected=["/opt/custom-protected"]))
    with pytest.raises(SandboxPolicyError):
        sb2.resolve("/opt/custom-protected/secret")


def test_sandbox_only_disables_shell_and_writes(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("sandbox-only"))
    r = run_shell(sb, RunShellArgs(command="ls"), timeout_sec=5, max_output_chars=1000)
    assert not r.ok
    assert "disabled" in r.payload["error"]
    assert not sb.write_allowed


def test_workspace_blocks_dangerous_commands(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    assert sb.check_shell("sudo rm -rf /") is not None
    assert sb.check_shell("printenv") is not None
    assert sb.check_shell("cat ~/.ssh/id_rsa") is not None
    assert sb.check_shell("ls -la") is None
    assert sb.check_shell("python3 -m pytest") is None


def test_protected_path_no_substring_false_positive(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    assert sb.check_shell("ls bindings") is None
    assert sb.check_shell("cat sbin_notes.txt") is None
    assert sb.check_shell("echo /usr/bin") is not None
    assert sb.check_shell("cat /etc/passwd") is not None


def test_protected_relative_token_resolved_against_workdir(tmp_path: Path) -> None:
    agent = AgentConfig(sandbox_mode="workspace", protected_paths=["secret"])
    sb = WorkdirSandbox(tmp_path, SparkConfig(agent=agent))
    assert sb.check_shell("cat secret/key.txt") is not None
    assert sb.check_shell("cat other/file.txt") is None


def test_full_access_shell_blocks_protected_allows_rest(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("full-access"))
    assert sb.check_shell("cat /etc/passwd") is not None
    assert sb.check_shell("cat /workspace/src/spark/config.py") is not None
    assert sb.check_shell("ls /opt/whatever") is None
    assert sb.check_shell("npm run fetch") is None


def test_full_access_allows_anything(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("full-access"))
    assert sb.check_shell("sudo anything") is None


def test_full_access_still_protects_spark_self(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("full-access"))
    with pytest.raises(SandboxPolicyError):
        sb.resolve("/workspace/src/spark/config.py")
    reason = sb.check_write_path(Path("/root/.spark/config.toml"))
    assert reason is not None


def test_unrestricted_allows_self_and_system(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("unrestricted"))
    assert sb.resolve("/workspace/src/spark/config.py") == Path("/workspace/src/spark/config.py")
    assert sb.check_shell("sudo anything") is None
    assert sb.check_shell("printenv") is None
    assert sb.check_write_path(Path("/root/.spark/config.toml")) is None
    assert sb.check_write_path(Path("/etc/hosts")) is None


def test_unrestricted_respects_user_protected_paths(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("unrestricted", protected=["/opt/custom-protected"]))
    with pytest.raises(SandboxPolicyError):
        sb.resolve("/opt/custom-protected/secret")
    assert sb.check_write_path(Path("/etc/hosts")) is None


def test_shell_runs_and_captures(tmp_path: Path) -> None:
    sb = WorkdirSandbox(tmp_path, _cfg("workspace"))
    r = run_shell(sb, RunShellArgs(command="echo hello"), timeout_sec=5, max_output_chars=1000)
    assert r.ok
    assert r.payload["stdout"].strip() == "hello"
