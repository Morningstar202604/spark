from __future__ import annotations

import logging
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from spark.config import SparkConfig
from spark.store import SessionStore
from spark.web.routes import build_handler_class
from spark.web.sse import _SSESession
from spark.web.state import SparkWebState, WebApprover
from spark.web.utils import (
    INDEX_HTML,
    MAX_REQUEST_BYTES,
    _cache_control,
    _port_in_use,
    _probe_target_allowed,
    _safe_agents_md_name,
    parse_mcp_servers,
    pick_free_port,
    ui_dir_for,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API — re-exported so that callers can do
#   from spark.web.server import build_web_server, serve_web, WebApprover, ...
# and tests can monkeypatch save_config / default_home on this module.
# ---------------------------------------------------------------------------
from spark.config import default_home, save_config  # noqa: E402

__all__ = [
    "build_web_server",
    "serve_web",
    "WebApprover",
    "ui_dir_for",
    "parse_mcp_servers",
    # Private re-exports kept for test backward-compatibility
    "_SSESession",
    "_cache_control",
    "_safe_agents_md_name",
    "_probe_target_allowed",
    "MAX_REQUEST_BYTES",
    "default_home",
    "save_config",
]


class SparkHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer that carries the web state on the server instance.

    The HTTP handler (built by ``build_handler_class``) retrieves the
    state via ``self.server.state`` and the UI directory via
    ``self.server.ui_dir``.
    """

    state: Any = None  # SparkWebState, set by _build_server_and_state
    ui_dir: Path | None = None


def _build_server_and_state(
    *,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> tuple[SparkHTTPServer, SparkWebState]:
    state = SparkWebState(workdir, cfg, store)
    ui_dir = ui_dir_for()
    html = (
        (ui_dir / "index.html").read_text(encoding="utf-8")
        if ui_dir
        else INDEX_HTML.read_text(encoding="utf-8")
    )
    HandlerClass = build_handler_class(html)

    if port is None:
        port = pick_free_port(host, 8000)
    elif _port_in_use(host, port):
        logger.error(
            "Port %d is already in use. Omit --port to auto-pick a free one, "
            "or pass --port <N> with a different port.",
            port,
        )
        raise SystemExit(1)

    server = SparkHTTPServer((host, port), HandlerClass)
    server.state = state
    server.ui_dir = ui_dir

    local_only = host in {"127.0.0.1", "localhost", "::1"}
    logger.info("Spark web at http://%s:%d session=%s", host, port, state.session_id)
    if not local_only:
        logger.warning(
            "Listening beyond loopback. Remote callers must send the "
            "X-Spark-Token header. The CLI prints the token to the console when "
            "binding beyond loopback; it is not written to the log because log "
            "collection would turn it into a leaked credential."
        )
    return server, state


def build_web_server(
    *,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> tuple[SparkHTTPServer, SparkWebState]:
    """构建但不启动 Web 服务；返回 (server, state) 供 serve_web 与桌面壳复用。"""
    return _build_server_and_state(
        workdir=workdir, cfg=cfg, store=store, host=host, port=port
    )


def serve_web(
    *,
    workdir: Path,
    cfg: SparkConfig,
    store: SessionStore,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> None:
    server, _state = build_web_server(
        workdir=workdir, cfg=cfg, store=store, host=host, port=port
    )
    try:
        server.serve_forever()
    finally:
        store.close()
