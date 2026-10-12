"""HTTP Handler class + route dispatch.  Kept thin: all business logic lives
in ``spark.web._handlers`` as standalone ``(handler, body) -> None`` functions.

The Handler class is responsible for:

* HTTP plumbing (reading request bodies, writing JSON responses)
* Security guards (loopback check, origin check, CSRF, auth token)
* Routing (GET → handler methods, POST → dispatch table)

This module is intentionally import-light so it can be used from test
suites without pulling in the full application stack.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any
from urllib.parse import urlparse

from spark.web._handlers import (  # noqa: F401  (re-export for backward-compat)
    _handle_agents_md,
    _handle_approval,
    _handle_cancel,
    _handle_chat,
    _handle_chat_stream,
    _handle_checkpoints_rollback,
    _handle_memory,
    _handle_models_activate,
    _handle_models_delete,
    _handle_models_save,
    _handle_sessions_delete,
    _handle_sessions_new,
    _handle_sessions_switch,
    _handle_settings,
    _handle_test,
)
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state  # noqa: E402

logger = logging.getLogger(__name__)


def build_handler_class(html: str):
    """Build a BaseHTTPRequestHandler subclass bound to *html* content."""

    class Handler(HandlerBase):
        def end_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            import sys

            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

        # ------------------------------------------------------------------
        # Security guards
        # ------------------------------------------------------------------
        def _is_loopback(self) -> bool:
            host = self.client_address[0] if self.client_address else ""
            return host in {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}

        def _host_is_local(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            if not host:
                return True  # HTTP/1.0 clients may omit it
            if host.startswith("["):  # IPv6 literal
                hostname = host[1:].split("]", 1)[0]
            else:
                hostname = host.rsplit(":", 1)[0] if ":" in host else host
            hostname = hostname.strip("[]").lower()
            return hostname in {"127.0.0.1", "::1", "localhost"}

        def _origin_allowed(self) -> bool:
            origin = (self.headers.get("Origin") or "").strip()
            if not origin:
                return True  # non-browser client (curl, TUI, SDK)
            try:
                parsed = urlparse(origin)
            except ValueError:
                return False
            host_c = (parsed.hostname or "").lower()
            return host_c in {"127.0.0.1", "::1", "localhost"}

        def _authorized(self) -> bool:
            import secrets as _secrets

            if self._is_loopback():
                return True
            supplied = (self.headers.get("X-Spark-Token") or "").encode("utf-8", "replace")
            state = _route_state(self)
            return bool(state.control_token) and _secrets.compare_digest(
                supplied, state.control_token.encode("utf-8")
            )

        def _guard(self, path: str) -> bool:
            if not self._host_is_local():
                self._json(403, {"error": "invalid host header"})
                return True
            if not self._origin_allowed():
                self._json(403, {"error": "cross-origin request rejected"})
                return True
            if self._is_loopback() or self._authorized():
                return False
            self._json(
                401,
                {
                    "error": "unauthorized",
                    "hint": "remote access requires the X-Spark-Token header",
                },
            )
            return True

        def _guard_mutation(self, path: str) -> bool:
            if self._guard(path):
                return True
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype and ctype != "application/json":
                self._json(
                    415,
                    {
                        "error": "unsupported content type",
                        "hint": "use application/json or the X-Spark-Token header",
                    },
                )
                return True
            return False

        # ------------------------------------------------------------------
        # Response helpers
        # ------------------------------------------------------------------
        def _json(self, code: int, body: dict | list) -> None:
            data = _json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _html(self, request_path: str = "/") -> None:
            from spark.web.utils import _cache_control

            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            cc = _cache_control(request_path)
            if cc:
                self.send_header("Cache-Control", cc)
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            from spark.web.utils import MAX_REQUEST_BYTES

            raw_length = (self.headers.get("Content-Length") or "0").strip()
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("invalid Content-Length") from exc
            if length < 0:
                raise ValueError("invalid Content-Length")
            if length > MAX_REQUEST_BYTES:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b"{}"
            return _json.loads(raw.decode("utf-8") or "{}")

        # ------------------------------------------------------------------
        # Route dispatch — GET
        # ------------------------------------------------------------------
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith("/api/") and self._guard(path):
                return
            self._handle_get(path)

        def _handle_get(self, path: str) -> None:
            from spark.web.utils import _CTYPES, _cache_control

            state = _route_state(self)
            ui_dir = getattr(self.server, "ui_dir", None)
            if path in {"/", "/index.html"}:
                self._html(path)
                return
            if ui_dir is not None and not path.startswith("/api/"):
                rel = path.lstrip("/")
                if rel:
                    target = (ui_dir / rel).resolve()
                    try:
                        target.relative_to(ui_dir.resolve())
                    except ValueError:
                        self._json(404, {"error": "not found"})
                        return
                    if target.is_file():
                        data = target.read_bytes()
                        ctype = _CTYPES.get(
                            target.suffix.lower(), "application/octet-stream"
                        )
                        self.send_response(200)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(data)))
                        cc = _cache_control(path)
                        if cc:
                            self.send_header("Cache-Control", cc)
                        self.end_headers()
                        self.wfile.write(data)
                        return
                self._html(path)
                return
            if path == "/api/status":
                self._json(200, state.status())
                return
            if path == "/api/config":
                state.ensure_default_profile()
                self._json(200, state.full_config())
                return
            if path == "/api/models":
                from spark.config import mask_secret

                state.ensure_default_profile()
                profiles = [
                    {
                        "id": p.id,
                        "name": p.name,
                        "provider": p.provider,
                        "base_url": p.base_url,
                        "model": p.model,
                        "api_key_masked": mask_secret(p.api_key) if p.api_key else "",
                        "has_api_key": bool(p.api_key),
                        "active": p.id == state.cfg.active_profile_id,
                    }
                    for p in state.cfg.model_profiles
                ]
                self._json(200, {"profiles": profiles})
                return
            if path == "/api/agents_md":
                path_md = state.workdir / state.cfg.context.agents_md
                content = (
                    path_md.read_text(encoding="utf-8") if path_md.exists() else ""
                )
                self._json(
                    200,
                    {
                        "filename": state.cfg.context.agents_md,
                        "exists": path_md.exists(),
                        "content": content,
                        "chars": len(content),
                        "max_fragment_chars": state.cfg.context.max_fragment_chars,
                    },
                )
                return
            if path == "/api/memory":
                query = urlparse(self.path).query
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                q = params.get("q", "")
                items = state.memory.list_memories()
                search_hits: list[dict] = []
                if q:
                    try:
                        for row, score in state.memory.search(q, top_k=8):
                            entry = row.to_dict()
                            entry["score"] = score
                            search_hits.append(entry)
                    except Exception as exc:
                        search_hits = [{"error": str(exc)}]
                self._json(
                    200,
                    {
                        "items": items,
                        "search": search_hits,
                        "stats": state.memory.stats(),
                    },
                )
                return
            if path == "/api/sessions":
                rows = state.store.list_sessions()
                sessions = [
                    {
                        k: row.get(k)
                        for k in (
                            "id",
                            "title",
                            "keywords",
                            "workdir",
                            "model",
                            "created_at",
                            "updated_at",
                        )
                    }
                    for row in rows
                    if row["workdir"] == str(state.workdir)
                ][:50]
                self._json(200, {"sessions": sessions, "current": state.session_id})
                return
            if path == "/api/checkpoints":
                cps = state.store.list_checkpoints(state.session_id)
                self._json(200, {"checkpoints": cps})
                return
            if path == "/api/history":
                messages = []
                if state.loop:
                    for msg in state.loop.history:
                        msg_entry: dict[str, Any]
                        if msg.role == "tool":
                            msg_entry = {
                                "role": "tool",
                                "name": msg.name,
                                "content": msg.content or "",
                            }
                        elif msg.role == "summary":
                            msg_entry = {
                                "role": "assistant",
                                "content": "[conversation summary]\n"
                                + (msg.content or ""),
                            }
                        elif msg.role == "user":
                            msg_entry = {"role": "user", "content": msg.content or ""}
                            if msg.images:
                                msg_entry["images"] = [
                                    f"data:{i.media_type};base64,{i.data}"
                                    for i in msg.images
                                ]
                            if not msg.content:
                                continue
                        elif msg.role == "assistant":
                            msg_entry = {"role": "assistant", "content": msg.content or ""}
                            if not msg.content:
                                continue
                        else:
                            continue
                        messages.append(msg_entry)
                self._json(200, {"session_id": state.session_id, "messages": messages})
                return
            self._json(404, {"error": "not found"})

        # ------------------------------------------------------------------
        # Route dispatch — POST
        # ------------------------------------------------------------------
        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if self._guard_mutation(path):
                return
            try:
                body = self._read_json()
            except _json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            except ValueError as exc:
                self._json(413, {"error": str(exc)})
                return

            handler_fn = _POST_HANDLERS.get(path)
            if handler_fn is not None:
                handler_fn(self, body)
                return
            self._json(404, {"error": "not found"})

    return Handler


# ======================================================================
# POST dispatch table — wired from spark.web._handlers
# ======================================================================
_POST_HANDLERS: dict[str, Any] = {
    "/api/models/save": _handle_models_save,
    "/api/models/delete": _handle_models_delete,
    "/api/models/activate": _handle_models_activate,
    "/api/agents_md": _handle_agents_md,
    "/api/memory": _handle_memory,
    "/api/cancel": _handle_cancel,
    "/api/checkpoints/rollback": _handle_checkpoints_rollback,
    "/api/approval": _handle_approval,
    "/api/sessions/new": _handle_sessions_new,
    "/api/sessions/delete": _handle_sessions_delete,
    "/api/sessions/switch": _handle_sessions_switch,
    "/api/chat/stream": _handle_chat_stream,
    "/api/chat": _handle_chat,
    "/api/settings": _handle_settings,
    "/api/test": _handle_test,
}
