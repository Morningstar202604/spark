"""P1：OpenAPI 合同层——前端 codegen 的单一事实来源。

用 FastAPI 的类型化路由**声明**核心 JSON API 的请求/响应模型，
生成 OpenAPI 3.1 schema，由现有 Web 服务在 ``/api/openapi.json`` 提供。
本模块只做"合同"，不接管运行时（现有 ThreadingHTTPServer 保持不变）；
FastAPI 全量迁移（P1.5）时同一份模型直接变成实现。

对齐对象：``web/routes.py`` 中 status / config / models / memory / sessions /
checkpoints 六个只读端点的真实返回结构。
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field


class StatusInfo(BaseModel):
    """state.status() 的运行态快照（键集以实现为准，宽松类型）。"""

    model_config = {"extra": "allow"}


class ModelProfile(BaseModel):
    id: str
    name: str
    provider: str
    base_url: str
    model: str
    api_key_masked: str = ""
    has_api_key: bool = False
    active: bool = False


class ModelsResponse(BaseModel):
    profiles: list[ModelProfile]


class MemoryItem(BaseModel):
    model_config = {"extra": "allow"}

    id: int
    content: str


class MemoryResponse(BaseModel):
    items: list[MemoryItem]
    search: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class SessionRow(BaseModel):
    id: str
    title: str = ""
    keywords: list[str] = Field(default_factory=list)
    workdir: str = ""
    model: str = ""
    created_at: int | float | None = None
    updated_at: int | float | None = None


class SessionsResponse(BaseModel):
    sessions: list[SessionRow]
    current: str | None = None


class CheckpointRow(BaseModel):
    model_config = {"extra": "allow"}


class CheckpointsResponse(BaseModel):
    checkpoints: list[CheckpointRow]


class ErrorResponse(BaseModel):
    error: str


app = FastAPI(
    title="Spark Web API",
    version="0.9",
    description=(
        "Spark 本地 Web 服务合同层。运行时由本地 token 鉴权；"
        "此 schema 供前端 codegen（orval/openapi-typescript）与未来 FastAPI 迁移复用。"
    ),
)


@app.get(
    "/api/status",
    response_model=StatusInfo,
    tags=["runtime"],
    summary="运行态快照（会话/循环/用量）",
)
def get_status() -> Any:  # pragma: no cover - 合同层，仅生成 schema
    ...


@app.get(
    "/api/config",
    response_model=dict[str, Any],
    tags=["runtime"],
    summary="完整配置（已脱敏）",
)
def get_config() -> Any:  # pragma: no cover
    ...


@app.get(
    "/api/models",
    response_model=ModelsResponse,
    tags=["models"],
    summary="模型档案列表（密钥脱敏）",
)
def get_models() -> Any:  # pragma: no cover
    ...


@app.get(
    "/api/memory",
    response_model=MemoryResponse,
    tags=["memory"],
    summary="记忆条目 + 可选搜索",
)
def get_memory(q: str = "") -> Any:  # pragma: no cover
    ...


@app.get(
    "/api/sessions",
    response_model=SessionsResponse,
    tags=["sessions"],
    summary="当前 workdir 的会话列表（≤50）",
)
def get_sessions() -> Any:  # pragma: no cover
    ...


@app.get(
    "/api/checkpoints",
    response_model=CheckpointsResponse,
    tags=["checkpoints"],
    summary="当前会话检查点",
)
def get_checkpoints() -> Any:  # pragma: no cover
    ...


@app.get(
    "/api/history",
    response_model=list[dict[str, Any]],
    tags=["sessions"],
    summary="当前会话消息历史",
)
def get_history() -> Any:  # pragma: no cover
    ...


def openapi_schema() -> dict[str, Any]:
    """生成 OpenAPI 3.1 JSON（由现有 Web 服务器在 /api/openapi.json 提供）。"""
    return app.openapi()
