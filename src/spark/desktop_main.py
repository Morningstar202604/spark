"""spark-desktop 专用入口。

windowed（console=False）构建里没有控制台，跑 TUI 是不可用的：Textual 的
终端控制序列写进黑洞，用户看到的只是一个没有任何窗口的进程。桌面产物必须
直接进 ``run_desktop``，命令行参数保持与 ``spark desktop`` 一致。
"""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    help="Spark 桌面版（原生窗口）。",
)


@app.callback()
def main(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: str | None = typer.Option(None, "--approval"),
    model: str | None = typer.Option(None, "--model"),
    provider: str | None = typer.Option(None, "--provider"),
    config: Path | None = typer.Option(None, "--config"),
    sandbox_mode: str | None = typer.Option(None, "--sandbox-mode"),
    port: int | None = typer.Option(None, "--port"),
    width: int = typer.Option(1280, "--width"),
    height: int = typer.Option(832, "--height"),
) -> None:
    from spark import cli as _cli
    from spark.desktop import run_desktop

    try:
        resolved = workdir.resolve()
        cfg = _cli._common_cfg(
            resolved, config, approval, model, provider, sandbox_mode=sandbox_mode
        )
        store = _cli._store()
        try:
            code = run_desktop(
                workdir=resolved,
                cfg=cfg,
                store=store,
                port=port,
                width=width,
                height=height,
            )
        finally:
            store.close()
        raise typer.Exit(code)
    except typer.Exit:
        # typer.Exit 是正常退出信号（含 pywebview 缺失的 2），不能被下面的
        # 兜底 except 吞成 1——那会让所有退出码失去区分度。
        raise
    except Exception as exc:  # noqa: BLE001 — 无控制台时只能落日志/弹窗
        # windowed 构建没有 stderr 可看，把失败写进桌面日志再以非零码退出，
        # 否则用户只会看到「点了没反应」。
        try:
            from spark.config import ensure_home

            log = ensure_home() / "desktop-crash.log"
            log.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except Exception:
            pass
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
