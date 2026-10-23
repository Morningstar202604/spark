"""P1：OpenAPI 合同层——前端 codegen 的单一事实来源。

用 FastAPI 的类型化路由**声明**核心 JSON API 的请求/响应模型，
生成 OpenAPI 3.1 schema，由现有 Web 服务在 ``/api/openapi.json`` 提供。
本模块只做"合同"，不接管运行时（现有 ThreadingHTTPServer 保持不变）；
FastAPI 全量迁移（P1.5）时同一份模型直接变成实现。

FastAPI 是**可选依赖**（``pip install 'spark-agent[openapi]'``）：只为生成
schema 而把完整 ASGI 框架塞进必装依赖不划算。未安装时
:func:`openapi_schema` 抛 :class:`OpenApiUnavailable`，路由层降级为 503。

对齐对象：``web/routes.py`` 中的只读端点——status / config / models / memory /
sessions / checkpoints / history，以及会话全文检索 sessions/search。
（写操作的 POST 端点尚未纳入合同；它们需要请求体模型，属于下一阶段。）
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class OpenApiUnavailable(RuntimeError):
    """FastAPI is not installed, so the OpenAPI contract cannot be generated."""


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


# Schema-only stubs: FastAPI only needs the signatures to emit the contract.
def _get_status() -> Any:  # pragma: no cover
    ...


def _get_config() -> Any:  # pragma: no cover
    ...


def _get_models() -> Any:  # pragma: no cover
    ...


def _get_memory(q: str = "") -> Any:  # pragma: no cover
    ...


def _get_sessions() -> Any:  # pragma: no cover
    ...


def _get_checkpoints() -> Any:  # pragma: no cover
    ...


def _get_history() -> Any:  # pragma: no cover
    ...


class SessionSearchHit(BaseModel):
    id: str
    workdir: str
    title: str
    updated_at: int
    match: dict[str, str]


class SessionSearchResponse(BaseModel):
    hits: list[SessionSearchHit] = Field(default_factory=list)


def _get_session_search(q: str = "", limit: int = 20) -> Any:  # pragma: no cover
    ...


_ROUTES: tuple[tuple[str, Any, str, str, Any], ...] = (
    ("/api/status", StatusInfo, "runtime", "运行态快照（会话/循环/用量）", _get_status),
    ("/api/config", dict[str, Any], "runtime", "完整配置（已脱敏）", _get_config),
    ("/api/models", ModelsResponse, "models", "模型档案列表（密钥脱敏）", _get_models),
    ("/api/memory", MemoryResponse, "memory", "记忆条目 + 可选搜索", _get_memory),
    ("/api/sessions", SessionsResponse, "sessions", "当前 workdir 的会话列表（≤50）", _get_sessions),
    ("/api/checkpoints", CheckpointsResponse, "checkpoints", "当前会话检查点", _get_checkpoints),
    ("/api/history", list[dict[str, Any]], "sessions", "当前会话消息历史", _get_history),
    (
        "/api/sessions/search",
        SessionSearchResponse,
        "sessions",
        "会话全文检索（FTS5 trigram；命中消息正文时带片段）",
        _get_session_search,
    ),
)

_APP: Any = None


def _build_app() -> Any:
    """Construct the declarative FastAPI app (requires the optional extra)."""
    global _APP
    if _APP is not None:
        return _APP
    try:
        from fastapi import FastAPI
    except ImportError as exc:  # pragma: no cover - exercised via the 503 path
        raise OpenApiUnavailable(
            "OpenAPI contract requires fastapi: pip install 'spark-agent[openapi]'"
        ) from exc

    app = FastAPI(
        title="Spark Web API",
        version="0.9",
        description=(
            "Spark 本地 Web 服务合同层。运行时由本地 token 鉴权；"
            "此 schema 供前端 codegen（orval/openapi-typescript）与未来 FastAPI 迁移复用。"
        ),
    )
    for path, model, tag, summary, handler in _ROUTES:
        app.add_api_route(
            path,
            handler,
            methods=["GET"],
            response_model=model,
            tags=[tag],
            summary=summary,
        )
    _APP = app
    return app


def openapi_schema() -> dict[str, Any]:
    """生成 OpenAPI 3.1 JSON（由现有 Web 服务器在 /api/openapi.json 提供）。"""
    return _build_app().openapi()
