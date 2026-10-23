"""`spark.desktop_main`：桌面专用入口。

这个入口的全部价值在于「windowed 构建里没有 stderr」：必须真的调
`run_desktop`（而不是落回 TUI），失败必须写日志而不是静默死掉。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from spark import desktop_main


class _FakeStore:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def test_entry_calls_run_desktop(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    """默认（无子命令）必须进桌面窗口，而不是 TUI。"""
    calls: dict[str, object] = {}
    store = _FakeStore()

    monkeypatch.setattr("spark.cli._common_cfg", lambda *a, **kw: object())
    monkeypatch.setattr("spark.cli._store", lambda: store)

    def fake_run_desktop(**kwargs):
        calls.update(kwargs)
        return 0

    monkeypatch.setattr("spark.desktop.run_desktop", fake_run_desktop)

    result = runner.invoke(desktop_main.app, [])
    assert result.exit_code == 0, result.output
    assert "workdir" in calls and "cfg" in calls
    assert calls["width"] == 1280 and calls["height"] == 832
    assert store.closed is True, "store 必须在退出前关闭"


def test_cli_options_forwarded(monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr("spark.cli._common_cfg", lambda *a, **kw: object())
    monkeypatch.setattr("spark.cli._store", lambda: _FakeStore())
    monkeypatch.setattr(
        "spark.desktop.run_desktop",
        lambda **kw: calls.update(kw) or 0,
    )

    result = runner.invoke(
        desktop_main.app,
        ["--workdir", str(tmp_path), "--port", "8123", "--width", "900", "--height", "700"],
    )
    assert result.exit_code == 0, result.output
    assert calls["port"] == 8123
    assert calls["width"] == 900 and calls["height"] == 700
    assert calls["workdir"] == tmp_path.resolve()


def test_run_desktop_nonzero_code_propagates(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    """pywebview 缺失时 run_desktop 返回 2，入口必须原样透出这个码。"""
    monkeypatch.setattr("spark.cli._common_cfg", lambda *a, **kw: object())
    monkeypatch.setattr("spark.cli._store", lambda: _FakeStore())
    monkeypatch.setattr("spark.desktop.run_desktop", lambda **kw: 2)

    result = runner.invoke(desktop_main.app, [])
    assert result.exit_code == 2


def test_failure_writes_crash_log(monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path) -> None:
    """windowed 构建没有控制台：失败必须落盘，否则用户只看到「点了没反应」。"""
    monkeypatch.setenv("SPARK_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "spark.cli._common_cfg",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = runner.invoke(desktop_main.app, [])
    assert result.exit_code == 1
    log = tmp_path / "home" / "desktop-crash.log"
    assert log.is_file()
    assert "RuntimeError" in log.read_text(encoding="utf-8")
    assert "boom" in log.read_text(encoding="utf-8")


def test_store_closed_even_when_run_desktop_raises(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    store = _FakeStore()
    monkeypatch.setattr("spark.cli._common_cfg", lambda *a, **kw: object())
    monkeypatch.setattr("spark.cli._store", lambda: store)

    def boom(**kw):
        raise RuntimeError("window failed")

    monkeypatch.setattr("spark.desktop.run_desktop", boom)

    result = runner.invoke(desktop_main.app, [])
    assert result.exit_code == 1
    assert store.closed is True


def test_app_help_mentions_desktop(runner: CliRunner) -> None:
    result = runner.invoke(desktop_main.app, ["--help"])
    assert result.exit_code == 0
    assert "桌面" in result.output or "desktop" in result.output.lower()
