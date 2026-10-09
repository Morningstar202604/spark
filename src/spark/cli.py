from __future__ import annotations

import asyncio
import sys
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
from spark.onboard import (
    SetupError,
    apply_setup,
    banner,
    config_as_dict,
    ensure_skeleton,
    friendly_config_error,
    health_exit_code,
    read_config,
    render_config_text,
    render_health,
    save,
    set_value,
    check_health,
    version_line,
)

from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    help="◆ Spark [Ember] — 本地编程智能体。新手从 spark init → spark doctor → spark web 开始。",
    epilog=(
        "常用流程：spark init 首次配置 · spark doctor 体检 · spark web 图形界面 · spark exec 脚本化"
    ),
    context_settings={"allow_extra_args": True},
)


def _store() -> SessionStore:
    ensure_home()
    write_config_template(default_home() / "config.toml")
    return SessionStore(default_home() / "sessions.db")


def _resolve_session(
    store: SessionStore, workdir: Path, cfg, resume: str | None
) -> str:
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
    cfg = load_config(
        config_path=config,
        workdir=workdir,
        approval=approval,
        model=model,
        provider=provider,
        sandbox_mode=sandbox_mode,
    )
    if cfg.provider.name == "openai_compat":
        try:
            require_api_key(cfg)
        except ConfigError:
            raise ConfigError(friendly_config_error(cfg)) from None
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
    workdir: Path = typer.Option(
        Path("."), "--workdir", dir_okay=True, file_okay=False
    ),
    approval: Optional[str] = typer.Option(None, "--approval"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
    sandbox_mode: Optional[str] = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )  # type: ignore[arg-type]
        prompt = " ".join(ctx.args).strip() or None
        store = _store()
        session_id = _resolve_session(store, workdir, cfg, None)
        _launch_tui(
            workdir=workdir,
            cfg=cfg,
            store=store,
            session_id=session_id,
            initial_prompt=prompt,
        )
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("exec", help="非交互执行单条 prompt，通过 stdout 输出最终结果（适合脚本与管道）")
def exec_cmd(
    prompt: str = typer.Argument(...),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    approval: Optional[str] = typer.Option(None, "--approval"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
    sandbox_mode: Optional[str] = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )  # type: ignore[arg-type]
        if cfg.agent.approval == "suggest":
            raise ConfigError(
                "spark exec cannot collect approvals; use --approval auto-edit or full-auto"
            )
        store = _store()
        session_id = store.create_session(
            workdir, cfg.provider.model, title=prompt[:80]
        )
        asyncio.run(_exec_async(prompt, workdir, cfg, store, session_id))
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


async def _exec_async(
    prompt: str, workdir: Path, cfg, store: SessionStore, session_id: str
) -> None:
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
    approval: Optional[str] = typer.Option(
        None,
        "--approval",
        help="suggest | auto-edit | full-auto（默认逐项询问，确认后再执行）",
    ),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    config: Optional[Path] = typer.Option(None, "--config"),
    sandbox_mode: Optional[str] = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="默认仅本机可访问；改为 0.0.0.0 会把控制面暴露到局域网/公网，风险自负",
    ),
    port: int = typer.Option(8000, "--port", help="Web 服务监听端口"),
) -> None:
    try:
        workdir = workdir.resolve()
        cfg = _common_cfg(
            workdir, config, approval, model, provider, sandbox_mode=sandbox_mode
        )  # type: ignore[arg-type]
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
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="User API key for the custom endpoint"
    ),
    rounds: int = typer.Option(2, "--rounds"),
) -> None:
    """Probe a custom OpenAI-compatible model and print real response text."""
    try:
        workdir = workdir.resolve()
        cfg = load_config(
            config_path=config, workdir=workdir, model=model, provider=provider
        )
        if base_url:
            cfg.provider.base_url = base_url
            cfg.provider.name = "openai_compat"
        if api_key:
            cfg.provider.api_key = api_key
        if cfg.provider.name == "mock":
            raise ConfigError(
                "spark test needs a real model; pass --provider openai_compat and --base-url"
            )
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


@app.command("sessions", help="列出所有会话，按更新时间倒序")
def sessions_cmd() -> None:
    store = _store()
    rows = store.list_sessions()
    store.close()
    if not rows:
        typer.echo("No sessions.")
        return
    for row in rows:
        typer.echo(
            f"{row['id']}\t{row['updated_at']}\t{row['workdir']}\t{row['title']}"
        )


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
        _launch_tui(
            workdir=workdir,
            cfg=cfg,
            store=store,
            session_id=session_id,
            initial_prompt=None,
        )
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(exc.exit_code) from exc


@app.command("version", help="输出当前语义化版本号")
def version_cmd() -> None:
    """Show the installed Spark version."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        detail = version("spark-agent")
    except PackageNotFoundError:
        detail = "development build"
    typer.echo(version_line(detail))


@app.command("init")
def init_cmd(
    config: Optional[Path] = typer.Option(None, "--config", help="写入的配置文件路径"),
    base_url: Optional[str] = typer.Option(
        None, "--base-url", help="模型接口地址，例如 https://api.agnes-ai.cn/v1"
    ),
    model: Optional[str] = typer.Option(None, "--model", help="模型名"),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="API Key（不会回显）"
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider", help="openai_compat | ollama | mock"
    ),
    approval: Optional[str] = typer.Option(
        None, "--approval", help="suggest | auto-edit | full-auto"
    ),
    sandbox_mode: Optional[str] = typer.Option(
        None,
        "--sandbox-mode",
        help="sandbox-only | workspace | full-access | unrestricted",
    ),
    probe: bool = typer.Option(True, "--probe/--no-probe", help="保存后立即测试连通性"),
) -> None:
    """Interactive setup: write provider credentials and verify them."""
    typer.echo(banner("首次配置向导"))
    try:
        workdir = Path.cwd()
        path = ensure_skeleton(config)
        cfg = read_config(config, workdir)
        changed = apply_setup(
            cfg,
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider=provider,
            approval=approval,
            sandbox_mode=sandbox_mode,
        )
        if not changed:
            if not sys.stdin.isatty():
                typer.echo(
                    "未提供任何配置项。使用 --base-url / --model / --api-key 指定，或直接编辑："
                )
                typer.echo(str(path))
                raise typer.Exit(2)
            _prompt_setup(cfg)
            changed = ["interactive"]
        written = save(cfg, config)
        typer.echo(f"已写入配置：{written}")
        for name in changed:
            typer.echo(f"  更新 {name}")
        typer.echo(
            f"  API Key：{'已设置（已隐藏）' if cfg.provider.api_key else '未设置'}"
        )
        if not probe:
            return
        typer.echo("正在测试连通性…")
        key = cfg.provider.api_key or _env_key(cfg) or ""
        result = asyncio.run(
            probe_provider(
                base_url=cfg.provider.base_url,
                api_key=key,
                model=cfg.provider.model,
                rounds=1,
            )
        )
        if result.ok:
            typer.echo(f"连通性正常（{result.latency_ms} ms）")
        else:
            typer.echo(f"连通性失败：{result.error or '未知错误'}", err=True)
            typer.echo("可用 spark doctor 查看完整诊断。", err=True)
            raise typer.Exit(1)
    except SetupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _env_key(cfg) -> str:
    import os

    return (
        os.environ.get(cfg.provider.api_key_env)
        or os.environ.get("SPARK_API_KEY")
        or ""
    )


def _prompt_setup(cfg) -> None:
    import getpass

    typer.echo("Spark 首次配置（直接回车跳过该项）")
    url = typer.prompt("模型接口地址", default=cfg.provider.base_url)
    cfg.provider.base_url = url.rstrip("/")
    cfg.provider.model = typer.prompt("模型名", default=cfg.provider.model)
    key = getpass.getpass("API Key（输入不回显，留空则不设置）: ").strip()
    if key:
        cfg.provider.api_key = key


@app.command("doctor")
def doctor_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Optional[Path] = typer.Option(None, "--config"),
    probe: bool = typer.Option(True, "--probe/--no-probe", help="实际调用一次模型验证"),
    as_json: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """Diagnose configuration and print actionable fixes."""
    typer.echo(banner("配置体检"))
    try:
        resolved = workdir.resolve()
    except OSError as exc:
        typer.echo(f"工作目录无效：{exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        cfg = read_config(config, resolved)
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    rows = check_health(
        cfg, workdir=resolved, config_path=config or (default_home() / "config.toml")
    )
    if (
        probe
        and cfg.provider.name != "mock"
        and (cfg.provider.api_key or _env_key(cfg))
    ):
        key = cfg.provider.api_key or _env_key(cfg)
        result = asyncio.run(
            probe_provider(
                base_url=cfg.provider.base_url,
                api_key=key,
                model=cfg.provider.model,
                rounds=1,
            )
        )
        if result.ok:
            rows.append(("ok", f"模型连通正常（{result.latency_ms} ms）", ""))
        else:
            rows.append(
                (
                    "bad",
                    f"模型连通失败：{result.error or '未知错误'}",
                    "核对 base_url / 模型名 / 密钥，或运行 spark init 重新配置",
                )
            )
    typer.echo(render_health(rows, as_json=as_json))
    raise typer.Exit(health_exit_code(rows))


config_app = typer.Typer(help="查看或修改配置", no_args_is_help=True)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show_cmd(
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Optional[Path] = typer.Option(None, "--config"),
    as_json: bool = typer.Option(False, "--json", help="输出机器可读 JSON"),
) -> None:
    """Print the effective configuration with secrets masked."""
    try:
        cfg = read_config(config, workdir.resolve())
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if as_json:
        import json

        typer.echo(json.dumps(config_as_dict(cfg), ensure_ascii=False, indent=2))
        return
    typer.echo(render_config_text(cfg))


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(..., help="点号路径，例如 agent.max_repeat_calls"),
    value: str = typer.Argument(..., help="新值"),
    workdir: Path = typer.Option(Path("."), "--workdir"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Set one configuration value and persist it."""
    try:
        cfg = read_config(config, workdir.resolve())
        set_value(cfg, key, value)
        written = save(cfg, config)
    except SetupError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except SparkError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    shown = "*" * len(value) if key.endswith("api_key") else value
    typer.echo(f"{key} = {shown}")
    typer.echo(f"已保存到 {written}")


@config_app.command("path", help="输出当前使用的配置文件绝对路径")
def config_path_cmd() -> None:
    """Print the configuration file location."""
    typer.echo(str(default_home() / "config.toml"))
