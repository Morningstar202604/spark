"""CLI：spark2 web / run / doctor / config。"""
from __future__ import annotations

import asyncio
import datetime
import shutil
from pathlib import Path

import typer

from spark2 import __version__
from spark2.approval import ApprovalGate
from spark2.config import APPROVAL_MODES, CONFIG_FILE, PRESETS, apply_preset, load_config, mask_key
from spark2.loop import AgentLoop
from spark2.provider import ProviderError, test_connection
from spark2.store import SessionStore
from spark2.tools import build_registry, tool_schemas
from spark2.tools.mcp import McpManager, servers_from_cfg

app = typer.Typer(add_completion=False, help="Spark Agent 重构版 —— 本地 AI 编程助手")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址（默认只本机，请勿随意改成 0.0.0.0）"),
    port: int = typer.Option(8000, "--port", help="监听端口"),
    workdir: str = typer.Option("", "--workdir", help="工作目录（可选，默认用配置里的）"),
) -> None:
    """启动 Web 界面。"""
    from spark2.web.server import app as fastapi_app

    cfg = load_config()
    if workdir:
        cfg["workdir"] = str(Path(workdir).expanduser().resolve())
        from spark2.config import save_config

        save_config(cfg)
    token = cfg.get("token", "")
    url = f"http://{host}:{port}/?token={token}"
    print(f"Spark {__version__} 已启动：")
    print(f"  地址：{url}")
    print("  令牌已内嵌在地址中；换浏览器/设备时用它访问。")
    print(f"  工作目录：{cfg.get('workdir')}")
    import uvicorn

    uvicorn.run(fastapi_app, host=host, port=port, log_level="warning")


class StdinGate(ApprovalGate):
    """无头模式：审批以终端问答形式进行（y=允许 / n=拒绝 / a=本次始终允许）。"""

    async def await_result(
        self, request_id: str, fut: asyncio.Future, timeout: float = 600.0
    ) -> bool:
        loop = asyncio.get_running_loop()
        label = self._request_tool or "操作"
        answer = await loop.run_in_executor(
            None, lambda: _ask(f"[审批] {label}：允许(y) / 拒绝(n) / 本次始终允许(a)？ [n] ")
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
    approval: str = typer.Option("suggest", "--approval", help=f"审批模式：{'/'.join(APPROVAL_MODES)}"),
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
    loop = AgentLoop(wd, provider_cfg, gate, max_context_tokens=int(cfg.get("max_context_tokens", 32000)))
    mcp: McpManager | None = None
    if cfg.get("mcp_servers"):
        mcp = McpManager(servers_from_cfg(cfg))
        loop.mcp = mcp
    from spark2.config import config_dir

    loop.log_path = config_dir() / "logs" / (datetime.date.today().isoformat() + ".jsonl")
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
                print("\n" + {"done": "完成", "cancelled": "已取消", "max_turns": "达到最大轮次", "error": "出错"}.get(ev.get("reason", ""), ev.get("reason", "")))
        return 0

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n已取消")
    finally:
        if mcp is not None:
            mcp.close()


@app.command()
def memory(
    query: str = typer.Argument(None, help="搜索关键词（不填则列出全部）"),
    workdir: str = typer.Option("", "--workdir", help="工作目录（默认用配置里的）"),
) -> None:
    """查看/搜索长期记忆。"""
    from spark2.memory import MemoryStore

    cfg = load_config()
    wd = str(Path(workdir or cfg.get("workdir") or Path.cwd()).expanduser().resolve())
    store = MemoryStore()
    if query:
        rows = store.search(wd, query, limit=20)
        print(f"[搜索] {query}（{wd}）→ {len(rows)} 条")
    else:
        rows = store.list(wd, limit=50)
        print(f"[记忆] {wd} 共 {store.count(wd)} 条")
    for r in rows:
        print(f"  - {r['key']}：{r['value']}（{r['created_at']}）")


@app.command()
def doctor() -> None:
    """体检：环境、配置、连接。"""
    from spark2.config import config_dir
    from spark2.memory import MemoryStore
    from spark2.tools.git import git_available, is_git_repo

    cfg = load_config()
    print(f"Spark {__version__}\n")
    print(f"配置文件：{CONFIG_FILE}" + ("（权限 600）" if _mode_ok() else ""))
    print(f"  模型服务：{PRESETS.get(cfg.get('provider', ''), {}).get('label', cfg.get('provider', '（自定义）'))} · 模型 {cfg.get('model') or '（未设置）'}")
    print(f"  API Key：{mask_key(cfg.get('api_key', '')) or '（未设置）'}")
    print(f"  工作目录：{cfg.get('workdir')}")
    print(f"  审批模式：{cfg.get('approval_mode')}")
    wd = Path(cfg.get("workdir", "")).expanduser()
    print(f"工作目录可访问：{'是' if wd.exists() else '否（不存在）'}")
    print(f"ripgrep：{'已安装' if shutil.which('rg') else '未安装（搜索将降级为 Python 遍历）'}")
    print(f"git（检查点）：{'已安装' if git_available() else '未安装（检查点功能不可用）'} · 工作目录{'是' if is_git_repo(wd) else '不是'} git 仓库")
    print(f"长期记忆：{MemoryStore().count(str(wd))} 条（{MemoryStore().path}）")
    print(f"事件日志：{config_dir() / 'logs'}（每次对话自动记录，JSONL 可复盘）")
    mcp_servers = cfg.get("mcp_servers") or []
    if mcp_servers:
        print(f"MCP：已配置 {len(mcp_servers)} 个服务器：{'、'.join(s.get('name', '?') for s in mcp_servers)}（连接在首个对话时建立）")
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
        return (CONFIG_FILE.stat().st_mode & 0o777) == 0o600
    except OSError:
        return False


@app.command()
def config() -> None:
    """打印当前配置（密钥打码）。"""
    cfg = load_config()
    print(f"配置文件：{CONFIG_FILE}")
    for k in ("provider", "base_url", "model", "workdir", "approval_mode", "max_context_tokens"):
        v = cfg.get(k, "")
        print(f"  {k} = {v}")
    print(f"  api_key = {mask_key(cfg.get('api_key', ''))}")
    mcp_servers = cfg.get("mcp_servers") or []
    if mcp_servers:
        print("  mcp_servers =")
        for s in mcp_servers:
            print(f"    - name={s.get('name')} command={s.get('command')} args={s.get('args')}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
