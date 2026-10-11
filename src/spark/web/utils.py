from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from spark.hooks import HookRegistry

logger = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).with_name("index.html")

HOOK_EVENTS = tuple(HookRegistry.EVENTS)

_WINDOWS_RESERVED = {"con", "prn", "aux", "nul"}

MAX_REQUEST_BYTES = 32 * 1024 * 1024

_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


def ui_dir_for() -> Path | None:
    for c in (
        Path(__file__).with_name("dist"),
        Path(__file__).resolve().parents[3] / "web" / "dist",
    ):
        if (c / "index.html").is_file():
            return c
    return None


def parse_mcp_servers(items: list) -> list:
    from spark.config import McpServerConfig

    out: list[McpServerConfig] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        command = str(item.get("command") or "").strip()
        if not command:
            raise ValueError("each MCP server needs name and command")
        args_raw = item.get("args")
        ro_raw = item.get("readonly_tools")
        out.append(
            McpServerConfig(
                name=name,
                command=command,
                args=[str(a) for a in args_raw] if isinstance(args_raw, list) else [],
                readonly_tools=[str(a) for a in ro_raw]
                if isinstance(ro_raw, list)
                else [],
            )
        )
    return out


def _cache_control(path: str) -> str | None:
    if path in {"/", "/index.html"}:
        return "no-cache"
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    return None


def _safe_agents_md_name(filename: str) -> str | None:
    """Accept only a bare filename inside the workdir. Rejects any path that could
    escape it (absolute, drive letter, UNC, separators, traversal, device names)."""
    if not filename or filename != filename.strip():
        return None
    if filename in {".", ".."} or len(filename) > 255:
        return None
    if any(sep in filename for sep in ("/", "\\", ":", "\x00")):
        return None
    if ".." in filename:
        return None
    stem = filename.split(".")[0].lower()
    if stem in _WINDOWS_RESERVED:
        return None
    return filename


def _probe_target_allowed(base_url: str, trusted_base_url: str) -> bool:
    """Only probe the same origin the user already configured; never let a request
    send the stored credential to an arbitrary host."""

    def host_of(value: str) -> str:
        try:
            return (urlparse(value).hostname or "").lower()
        except ValueError:
            return ""

    target = host_of(base_url)
    trusted = host_of(trusted_base_url)
    if not target or not trusted:
        return False
    return target == trusted


def _port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _extract_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def _event_payload(event) -> dict:
    payload: dict = {"type": event.type}
    if event.text:
        payload["text"] = event.text
    if event.tool_call:
        payload["tool"] = {
            "id": event.tool_call.id,
            "name": event.tool_call.name,
            "arguments": event.tool_call.arguments,
        }
    if event.result:
        payload["ok"] = event.result.ok
        payload["result"] = event.result.payload
    if event.approval:
        payload["approval"] = {
            "summary": event.approval.summary,
            "diff": event.approval.diff,
        }
    if event.data:
        payload["data"] = event.data
    return payload
