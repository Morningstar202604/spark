from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spark import cli
from spark.config import SparkConfig, load_config

runner = CliRunner()


def test_version_command_reports_package_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert "spark" in result.stdout.lower()


def test_init_writes_config_without_touching_user_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(cli, "default_home", lambda: home)
    cfg_path = tmp_path / "spark.toml"
    result = runner.invoke(
        cli.app,
        [
            "init",
            "--config",
            str(cfg_path),
            "--base-url",
            "https://example.invalid/v1",
            "--model",
            "demo-model",
            "--api-key",
            "sk-test-1234567890",
            "--no-probe",
        ],
    )
    assert result.exit_code == 0, result.stdout
    saved = cfg_path.read_text(encoding="utf-8")
    assert "example.invalid" in saved
    assert "demo-model" in saved
    assert "sk-test-1234567890" in saved


def test_init_never_echoes_the_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    result = runner.invoke(
        cli.app,
        [
            "init",
            "--config",
            str(cfg_path),
            "--base-url",
            "https://example.invalid/v1",
            "--model",
            "demo",
            "--api-key",
            "sk-supersecret-value-0001",
            "--no-probe",
        ],
    )
    assert result.exit_code == 0
    assert "sk-supersecret-value-0001" not in result.stdout


def test_init_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    args = [
        "init",
        "--config",
        str(cfg_path),
        "--base-url",
        "https://example.invalid/v1",
        "--model",
        "demo",
        "--api-key",
        "sk-abc-123",
        "--no-probe",
    ]
    assert runner.invoke(cli.app, args).exit_code == 0
    assert runner.invoke(cli.app, args).exit_code == 0


def test_doctor_passes_with_complete_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    runner.invoke(
        cli.app,
        [
            "init",
            "--config",
            str(cfg_path),
            "--base-url",
            "https://example.invalid/v1",
            "--model",
            "demo",
            "--api-key",
            "sk-abc-123456",
            "--no-probe",
        ],
    )
    result = runner.invoke(cli.app, ["doctor", "--config", str(cfg_path), "--no-probe"])
    assert result.exit_code == 0, result.stdout
    assert "配置" in result.stdout or "config" in result.stdout.lower()


def test_doctor_reports_missing_key_with_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "https://example.invalid/v1"\n'
        'model = "demo"\napi_key_env = "SPARK_API_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["doctor", "--config", str(cfg_path), "--no-probe"])
    assert result.exit_code == 1
    assert "spark init" in result.stdout


def test_doctor_warns_when_workdir_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "m"\n', encoding="utf-8")
    missing = tmp_path / "does-not-exist"
    result = runner.invoke(
        cli.app,
        ["doctor", "--config", str(cfg_path), "--workdir", str(missing), "--no-probe"],
    )
    assert "工作目录" in result.stdout or "workdir" in result.stdout.lower()


def test_config_show_masks_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text(
        '[provider]\nname = "openai_compat"\nbase_url = "https://x.invalid/v1"\n'
        'model = "m"\napi_key = "sk-should-not-appear-1234"\n',
        encoding="utf-8",
    )
    result = runner.invoke(cli.app, ["config", "show", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "sk-should-not-appear-1234" not in result.stdout
    assert "sk-s" in result.stdout


def test_config_set_updates_dotted_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "m"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["config", "set", "agent.max_repeat_calls", "6", "--config", str(cfg_path)],
    )
    assert result.exit_code == 0, result.stdout
    loaded = load_config(config_path=cfg_path, workdir=tmp_path)
    assert loaded.agent.max_repeat_calls == 6


def test_config_set_rejects_unknown_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["config", "set", "agent.nope", "1", "--config", str(cfg_path)],
    )
    assert result.exit_code == 2
    assert "nope" in (result.stdout + result.stderr)


def test_config_set_rejects_bad_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app,
        ["config", "set", "agent.max_repeat_calls", "abc", "--config", str(cfg_path)],
    )
    assert result.exit_code == 2


def test_missing_key_error_is_friendly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    cfg = SparkConfig()
    cfg.provider.name = "openai_compat"
    cfg.provider.api_key = None
    message = cli.friendly_config_error(cfg)
    assert "spark init" in message
    assert "API" in message


def test_json_output_mode_is_machine_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "default_home", lambda: tmp_path / "home")
    cfg_path = tmp_path / "spark.toml"
    cfg_path.write_text('[provider]\nname = "mock"\nmodel = "m"\n', encoding="utf-8")
    result = runner.invoke(
        cli.app, ["config", "show", "--config", str(cfg_path), "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["provider"]["name"] == "mock"


def test_help_lists_new_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for command in ("init", "doctor", "config", "version"):
        assert command in result.stdout
