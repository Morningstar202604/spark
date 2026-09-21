from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer

from spark.config import (
    ApprovalMode,
    ProviderName,
    default_home,
    ensure_home,
    load_config,
    require_api_key,
    write_config_template,
)
from spark.providers.factory import create_provider
from spark.providers.probe import probe_provider
from spark.core.loop import AgentLoop
from spark.errors import ConfigError, SparkError

from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    help="Spark local coding agent",
    context_settings={"allow_extra_args": True},
)


def _store() -> SessionStore:
    ensure_home()
    write_config_template(default_home() / "config.toml")
    return SessionStore(default_home() / "sessions.db")


def _resolve_session(store: SessionStore, workdir: Path, cfg, resume: str | None) -> str:
    if resume:
        row = store.get_session(resume)
        if not row:
            raise ConfigError(f"Unknown session id: {resume}")
        return resume
    return store.create_session(workdir, cfg.provider.model)


async def _boot_mcp(cfg, registry: ToolRegistry) -> McpBridge:
    bridge = McpBridge()
    if cfg.mcp_servers:
        await bridge.start(cfg.mcp_servers, registry)
    return bridge


def _common_cfg(
    workdir: Path,
    config: Path | None,
    approval: ApprovalMode | None,
    model: str | None,
    provider: ProviderName | None,
    sandbox_mode=None,
):
    cfg = load_config(config_path=config, workdir=workdir, approval=approval, model=model, provider=provider, sandbox_mode=sandbox_mode)
    if cfg.provider.name == "openai_compat":
        require_api_key(cfg)
    return cfg


def _launch_tui(
    *,
    workdir: Path,
    cfg,
    store: SessionStore,
    session_id: str,
    initial_prompt: str | None,
) -> None:
    from spark.tui.app import SparkApp

    prov = create_provider(cfg)
    sandbox = WorkdirSandbox(workdir, cfg)
    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    bridge = asyncio.run(_boot_mcp(cfg, registry))
    tool_ctx.mcp_call = bridge.call
    SparkApp(
        workdir=workdir,
        cfg=cfg,
        store=store,
        session_id=session_id,
        provider=prov,
        registry=registry,
        bridge=bridge,
        initial_prompt=initial_prompt,
    ).run()


@app.callback()
def main(
    ctx: typer.Context,
    workdir: Path = typer.Option(Path("."), "--workdir", dir_okay=True, file_okay=False),
    approval: Optional[str] = typer.Option(None, "--approval"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
    sandbox_mode: Optional[str] = typer.Option(None, "--sandbox-mode", help="sandbox-only | workspace | full-access | unrestricted"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(workdir, config, approval, model, provider, sandbox_mode=sandbox_mode)  # type: ignore[arg-type]
        prompt = " ".join(ctx.args).strip() or None
        store = _store()
        session_id = _resolve_session(store, workdir, cfg, None)
        _launch_tui(workdir=workdir, cfg=cfg, store=store, session_id=session_id, initial_prompt=prompt)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("exec")
def exec_cmd(
    prompt: str = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: Optional[str] = typer.Option(None, "--approval"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
    sandbox_mode: Optional[str] = typer.Option(None, "--sandbox-mode", help="sandbox-only | workspace | full-access | unrestricted"),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(workdir, config, approval, model, provider, sandbox_mode=sandbox_mode)  # type: ignore[arg-type]
        if cfg.agent.approval == "suggest":
            raise ConfigError("spark exec cannot collect approvals; use --approval auto-edit or full-auto")
        store = _store()
        session_id = store.create_session(workdir, cfg.provider.model, title=prompt[:80])
        asyncio.run(_exec_async(prompt, workdir, cfg, store, session_id))
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


async def _exec_async(prompt: str, workdir: Path, cfg, store: SessionStore, session_id: str) -> None:
    prov = create_provider(cfg)
    sandbox = WorkdirSandbox(workdir, cfg)
    tool_ctx = ToolContext(sandbox=sandbox, config=cfg)
    registry = ToolRegistry(tool_ctx)
    bridge = await _boot_mcp(cfg, registry)
    tool_ctx.mcp_call = bridge.call
    loop = AgentLoop(
        workdir=workdir,
        cfg=cfg,
        provider=prov,
        registry=registry,
        store=store,
        session_id=session_id,
        approver=None,
    )
    try:
        events = await loop.run(prompt)
        final = ""
        for event in events:
            if event.type == "turn_end" and event.text:
                final = event.text
            if event.type == "turn_error" and event.text:
                typer.echo(event.text, err=True)
                raise typer.Exit(1)
        typer.echo(final)
    finally:
        await bridge.close()
        store.close()


@app.command("web")
def web_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: Optional[str] = typer.Option("full-auto", "--approval"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
    sandbox_mode: Optional[str] = typer.Option(None, "--sandbox-mode", help="sandbox-only | workspace | full-access | unrestricted"),
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(workdir, config, approval, model, provider, sandbox_mode=sandbox_mode)  # type: ignore[arg-type]
        store = _store()
        from spark.web.server import serve_web

        serve_web(workdir=workdir, cfg=cfg, store=store, host=host, port=port)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("test")
def test_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
    base_url: Optional[str] = typer.Option(None, "--base-url"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="User API key for the custom endpoint"),
    rounds: int = typer.Option(2, "--rounds"),
) -> None:
    """Probe a custom OpenAI-compatible model and print real response text."""
    try:
        workdir = workdir.resolve()
        cfg = load_config(config_path=config, workdir=workdir, model=model, provider=provider)
        if base_url:
            cfg.provider.base_url = base_url
            cfg.provider.name = "openai_compat"
        if api_key:
            cfg.provider.api_key = api_key
        if cfg.provider.name == "mock":
            raise ConfigError("spark test needs a real model; pass --provider openai_compat and --base-url")
        key = require_api_key(cfg)
        result = asyncio.run(
            probe_provider(
                base_url=cfg.provider.base_url,
                api_key=key or "",
                model=cfg.provider.model,
                rounds=rounds,
            )
        )
        if result.ok:
            typer.echo("OK")
            typer.echo(f"model: {result.model}")
            typer.echo(f"base_url: {result.base_url}")
            typer.echo(f"latency_ms: {result.latency_ms}")
            typer.echo(f"rounds: {result.rounds}")
            typer.echo("content:")
            typer.echo(result.content)
            typer.echo("stream_content:")
            typer.echo(result.stream_content)
            raise typer.Exit(0)
        typer.echo("FAIL", err=True)
        typer.echo(result.error or "probe failed", err=True)
        if result.content:
            typer.echo("partial content:", err=True)
            typer.echo(result.content, err=True)
        raise typer.Exit(1)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("sessions")
def sessions_cmd() -> None:
    store = _store()
    rows = store.list_sessions()
    store.close()
    if not rows:
        typer.echo("No sessions.")
        return
    for row in rows:
        typer.echo(f"{row['id']}\t{row['updated_at']}\t{row['workdir']}\t{row['title']}")


@app.command("resume")
def resume_cmd(
    session_id: str = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: Optional[str] = typer.Option(None, "--approval"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(workdir, config, approval, model, provider)  # type: ignore[arg-type]
        store = _store()
        session_id = _resolve_session(store, workdir, cfg, session_id)
        row = store.get_session(session_id)
        if row:
            workdir = Path(row["workdir"])
        _launch_tui(workdir=workdir, cfg=cfg, store=store, session_id=session_id, initial_prompt=None)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc
