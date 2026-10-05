"""CLI：spark web / run / tui / doctor / config / memory。"""

from __future__ import annotations

import asyncio
import datetime
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from spark import __version__
from spark.approval import ApprovalGate
from spark.config import (
    APPROVAL_MODES,
    PRESETS,
    config_file,
    load_config,
    mask_key,
)
from spark.loop import AgentLoop
from spark.provider import test_connection
from spark.tools.mcp import McpManager, servers_from_cfg

app = typer.Typer(add_completion=False, help="Spark —— 本地 AI 编程助手（输入 spark 即开）")


@app.callback(invoke_without_command=True)
def _default_start(ctx: typer.Context) -> None:
    """傻瓜式：不指定子命令时，默认直接打开 Web 界面（像 opencode 一样即开即用）。"""
    if ctx.invoked_subcommand is None:
        # 显式传默认值：typer 的 Option 默认是 OptionInfo 对象，不能直接当参数用
        web(host="127.0.0.1", port=8000, log_level="info", workdir="")


def _ensure_web_built() -> None:
    """傻瓜式兜底：Web 前端未构建时自动构建，避免打开即空白页。

    前端是 React/Vite，server 优先服务 web/dist 下的构建产物；若 dist 缺失，
    服务器会退回提供源码 index.html，浏览器因无法加载 .tsx 而白屏。这里在
    启动前确保 dist/index.html 存在：缺依赖就装、缺构建就跑，失败再给明确指引。
    """
    web_dir = Path(__file__).resolve().parent / "web"
    dist_index = web_dir / "dist" / "index.html"
    if dist_index.exists():
        return
    print("[web] 检测到前端未构建，正在自动配置（首次约 1 分钟，只需一次）…")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm or not (shutil.which("node") or shutil.which("node.exe")):
        typer.secho(
            "[web] 未检测到 node/npm，无法自动构建前端。\n"
            f"  请手动在目录执行：cd \"{web_dir}\" && npm install && npm run build\n"
            "  完成后重试：spark web",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    try:
        if not (web_dir / "node_modules").exists():
            print("[web] 正在安装前端依赖（npm install）…")
            subprocess.run([npm, "install"], cwd=str(web_dir), check=True, timeout=1800)
        print("[web] 正在构建前端（npm run build）…")
        subprocess.run([npm, "run", "build"], cwd=str(web_dir), check=True, timeout=1800)
    except Exception as exc:
        typer.secho(
            f"[web] 前端自动构建失败：{exc}\n"
            f"可手动执行：cd \"{web_dir}\" && npm install && npm run build",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from exc  # 保留 npm 原始异常链，便于排障
    if not (web_dir / "dist" / "index.html").exists():
        typer.secho("[web] 构建完成但未找到产物 dist/index.html，请检查前端构建日志。", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command()
def web(
    host: str = typer.Option(
        "127.0.0.1", "--host", help="监听地址（默认仅本机；监听 0.0.0.0 等非回环地址时必须先设置访问令牌）"
    ),
    port: int = typer.Option(8000, "--port", help="监听端口"),
    log_level: str = typer.Option(
        "info", "--log-level", help="uvicorn 日志级别（info/warning/error），线上排障建议 info"
    ),
    workdir: str = typer.Option(
        "", "--workdir", help="工作目录（可选，默认用配置里的）"
    ),
) -> None:
    """启动 Web 界面。"""
    _ensure_web_built()
    from spark.web.server import app as fastapi_app

    cfg = load_config()
    if workdir:
        cfg["workdir"] = str(Path(workdir).expanduser().resolve())
        from spark.config import save_config

        save_config(cfg)
    token = (cfg.get("token") or "").strip()
    # 安全默认：无令牌时只允许回环地址，避免把命令执行能力免登录暴露给整个网络
    if not token and not is_loopback_host(host):
        typer.secho(
            f"[web] 拒绝启动：监听 {host} 但未设置访问令牌，任何可达者都能让 Agent 执行命令。\n"
            "  先设置令牌（配置文件 token 字段，或以 127.0.0.1 启动后在网页设置里开启），\n"
            "  或去掉 --host 仅本机使用。",
            fg=typer.colors.RED,
        )
        raise typer.Exit(2)
    # 打印 127.0.0.1 地址便于本机浏览器直接打开
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{port}/" + (f"?token={token}" if token else "")
    print(f"Spark {__version__} 已启动：")
    print(f"  地址：{url}")
    if token:
        print("  访问令牌已内嵌在地址中；换浏览器/设备时用它访问。")
    else:
        print("  未设置访问令牌：仅本机可访问；可在网页设置里开启。")
    print(f"  工作目录：{cfg.get('workdir')}")
    import uvicorn

    from spark.trace import configure_logging as _configure_trace_logging
    _configure_trace_logging()

    uvicorn.run(fastapi_app, host=host, port=port, log_level=log_level)


def is_loopback_host(host: str) -> bool:
    """是否为仅本机可达的监听地址（localhost / 127.0.0.0/8 / ::1）。"""
    import ipaddress

    h = (host or "").strip().strip("[]")
    if h.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


class StdinGate(ApprovalGate):
    """无头模式：审批以终端问答形式进行（y=允许 / n=拒绝 / a=本次始终允许）。"""

    async def await_result(
        self, request_id: str, fut: asyncio.Future, timeout: float = 600.0
    ) -> bool:
        loop = asyncio.get_running_loop()
        label = self._request_tool or "操作"
        answer = await loop.run_in_executor(
            None,
            lambda: _ask(f"[审批] {label}：允许(y) / 拒绝(n) / 本次始终允许(a)？ [n] "),
        )
        if answer == "a" and self._request_tool:
            self.always.add(self._request_tool)
            return True
        return answer == "y"


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip().lower() or "n"
    except (EOFError, KeyboardInterrupt):
        return "n"


@app.command()
def run(
    prompt: str,
    workdir: str = typer.Option(".", "--workdir", help="工作目录"),
    approval: str = typer.Option(
        "suggest", "--approval", help=f"审批模式：{'/'.join(APPROVAL_MODES)}"
    ),
    model: str = typer.Option("", "--model", help="模型名（覆盖配置）"),
) -> None:
    """无头模式：在终端里跑一次对话（写入/命令需逐条确认）。"""
    cfg = load_config()
    if model:
        cfg["model"] = model
    wd = Path(workdir).expanduser().resolve()
    if not wd.exists():
        typer.secho(f"工作目录不存在：{wd}", fg=typer.colors.RED)
        raise typer.Exit(1)
    gate = StdinGate(mode=approval if approval in APPROVAL_MODES else "suggest")
    provider_cfg = {
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "api_key": cfg.get("api_key", ""),
    }
    loop = AgentLoop(
        wd,
        provider_cfg,
        gate,
        max_context_tokens=int(cfg.get("max_context_tokens", 32000)),
    )
    mcp: McpManager | None = None
    if cfg.get("mcp_servers"):
        mcp = McpManager(servers_from_cfg(cfg))
        loop.mcp = mcp
    from spark.config import config_dir

    loop.log_path = (
        config_dir() / "logs" / (datetime.date.today().isoformat() + ".jsonl")
    )
    messages: list[dict] = [{"role": "user", "content": prompt}]
    print(f"[工作目录] {wd}")
    print(f"[模型] {cfg.get('model') or '(未配置)'}\n")

    async def _run() -> int:
        async for ev in loop.stream(messages):
            t = ev.get("type")
            if t == "text":
                print(ev.get("delta", ""), end="", flush=True)
            elif t == "reasoning":
                pass
            elif t == "plan":
                print("\n[计划] " + " → ".join(ev.get("steps", [])))
            elif t == "tool_start":
                print(f"\n[工具] {ev.get('name')} {ev.get('args_summary', '')}")
            elif t == "tool_result":
                print(f"\n[结果] {ev.get('output', '')[:2000]}")
            elif t == "error":
                print(f"\n[错误] {ev.get('message', '')}")
            elif t == "done":
                print(
                    "\n"
                    + {
                        "done": "完成",
                        "cancelled": "已取消",
                        "max_turns": "达到最大轮次",
                        "error": "出错",
                    }.get(ev.get("reason", ""), ev.get("reason", ""))
                )
        return 0

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n已取消")
    finally:
        if mcp is not None:
            mcp.close()


@app.command()
def tui() -> None:
    """终端界面：键盘优先的全屏 UI（Ctrl+N 新建 / Ctrl+S 会话 / A 允许 / D 拒绝）。"""
    try:
        from spark.tui import main as tui_main
    except ImportError:
        typer.secho(
            'TUI 缺少依赖：pip install "spark-agent[dev]" 或 pip install "textual>=0.60"',
            fg=typer.colors.RED,
        )
        raise typer.Exit(1) from None
    tui_main(load_config())


@app.command()
def memory(
    query: str = typer.Argument(None, help="搜索关键词（不填则列出全部）"),
    workdir: str = typer.Option("", "--workdir", help="工作目录（默认用配置里的）"),
) -> None:
    """查看/搜索长期记忆。"""
    from spark.memory import MemoryStore

    cfg = load_config()
    wd = str(Path(workdir or cfg.get("workdir") or Path.cwd()).expanduser().resolve())
    store = MemoryStore()
    if query:
        rows = store.search(wd, query, limit=20)
        print(f"[搜索] {query}（{wd}）→ {len(rows)} 条")
    else:
        rows = store.list_memories(wd, limit=50)
        print(f"[记忆] {wd} 共 {store.count(wd)} 条")
    for r in rows:
        print(f"  - {r['key']}：{r['value']}（{r['created_at']}）")


@app.command()
def doctor() -> None:
    """体检：环境、配置、连接。"""
    from spark.config import config_dir
    from spark.memory import MemoryStore
    from spark.tools.git import git_available, is_git_repo

    cfg = load_config()
    print(f"Spark {__version__}\n")
    print(f"配置文件：{config_file()}" + _perm_note())
    print(
        f"  模型服务：{PRESETS.get(cfg.get('provider', ''), {}).get('label', cfg.get('provider', '（自定义）'))} · 模型 {cfg.get('model') or '（未设置）'}"
    )
    print(f"  API Key：{mask_key(cfg.get('api_key', '')) or '（未设置）'}")
    _tok = (cfg.get("token") or "").strip()
    print(
        f"  访问令牌：{'已设置（请求需携带）' if _tok else '未设置（本机免登录；在网页设置里可开启）'}"
    )
    print(f"  工作目录：{cfg.get('workdir')}")
    print(f"  审批模式：{cfg.get('approval_mode')}")
    wd = Path(cfg.get("workdir", "")).expanduser()
    print(f"工作目录可访问：{'是' if wd.exists() else '否（不存在）'}")
    print(
        f"ripgrep：{'已安装' if shutil.which('rg') else '未安装（搜索将降级为 Python 遍历）'}"
    )
    print(
        f"git（检查点）：{'已安装' if git_available() else '未安装（检查点功能不可用）'} · 工作目录{'是' if is_git_repo(wd) else '不是'} git 仓库"
    )
    print(f"长期记忆：{MemoryStore().count(str(wd))} 条（{MemoryStore().path}）")
    print(f"事件日志：{config_dir() / 'logs'}（每次对话自动记录，JSONL 可复盘）")
    mcp_servers = cfg.get("mcp_servers") or []
    if mcp_servers:
        print(
            f"MCP：已配置 {len(mcp_servers)} 个服务器：{'、'.join(s.get('name', '?') for s in mcp_servers)}（连接在首个对话时建立）"
        )
    elif McpManager([]).available:
        print("MCP：未配置（在 config.toml 加 mcp_servers 即可启用，见 README）")
    else:
        print("MCP：未安装 mcp SDK（pip install mcp）")
    if cfg.get("model") == "mock":
        print("连接：演示模式，无需密钥。")
        return
    ok, msg = asyncio.run(test_connection(cfg))
    print(f"模型连接：{'成功' if ok else '失败 ' + msg}")


def _mode_ok() -> bool:
    try:
        return (config_file().stat().st_mode & 0o777) == 0o600
    except OSError:
        return False


def _perm_ok() -> bool:
    """配置文件权限是否达标：POSIX = 0600；Windows = ACL 收紧到仅当前用户。"""
    f = config_file()  # 动态：遵守 SPARK_HOME
    if sys.platform == "win32":
        from spark.config import win_acl_restricted

        return win_acl_restricted(f)
    return _mode_ok()


def _perm_note() -> str:
    if not config_file().exists() or not _perm_ok():
        return ""
    return "（ACL 已收紧：仅当前用户）" if sys.platform == "win32" else "（权限 600）"


@app.command()
def config() -> None:
    """打印当前配置（密钥打码）。"""
    cfg = load_config()
    print(f"配置文件：{config_file()}")
    for k in (
        "provider",
        "base_url",
        "model",
        "workdir",
        "approval_mode",
        "max_context_tokens",
    ):
        v = cfg.get(k, "")
        print(f"  {k} = {v}")
    print(f"  api_key = {mask_key(cfg.get('api_key', ''))}")
    mcp_servers = cfg.get("mcp_servers") or []
    if mcp_servers:
        print("  mcp_servers =")
        for s in mcp_servers:
            print(
                f"    - name={s.get('name')} command={s.get('command')} args={s.get('args')}"
            )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
