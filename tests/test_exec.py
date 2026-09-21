from pathlib import Path

from typer.testing import CliRunner

from spark.cli import app

runner = CliRunner()


def test_exec_suggest_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["exec", "--workdir", str(tmp_path), "--provider", "mock", "--approval", "suggest", "hello"],
    )
    assert result.exit_code == 2
    assert "cannot collect approvals" in result.output


def test_exec_mock_full_auto(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["exec", "--workdir", str(tmp_path), "--provider", "mock", "--approval", "full-auto", "hello"],
    )
    assert result.exit_code == 0
    assert "Spark" in result.output or "Done" in result.output or result.output.strip() != ""
