"""FastAPI 服务主装配：挂载各 APIRouter 模块 + 单页 + 静态资源。

各兄弟模块职责：
- api_common.py   AppState 与鉴权/辅助（check_token/json_body/config_payload/sse 等）
- api_config.py   配置 / 测试连接 / 最近目录 / 用量 / 插件
- api_sessions.py 会话 CRUD / 分叉
- api_data.py     记忆 / 文件浏览 / git 检查点
- api_chat.py     对话流式（SSE）/ 审批 / 取消
- api_terminal.py 内置终端（WS PTY + 关闭 tab）
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from spark import __version__

from .api_chat import router as router_chat
from .api_common import AppState
from .api_config import router as router_config
from .api_data import router as router_data
from .api_sessions import router as router_sessions
from .api_terminal import router as router_terminal

WEB_DIR = Path(__file__).parent
DIST_DIR = WEB_DIR / "dist"
STATIC_DIR = DIST_DIR if DIST_DIR.exists() else WEB_DIR


def create_app(state: AppState | None = None) -> FastAPI:
    state = state or AppState()
    app = FastAPI(title="Spark Agent", version=__version__)
    app.state.spark = state

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # 按域组装路由（各模块自行鉴权）
    app.include_router(router_config)
    app.include_router(router_sessions)
    app.include_router(router_data)
    app.include_router(router_chat)
    app.include_router(router_terminal)

    # 静态资源（放在路由之后，作为兜底）
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app


# 供 uvicorn 直接使用：python -m spark.web
app = create_app()
