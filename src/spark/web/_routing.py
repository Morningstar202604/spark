"""Shared routing helpers — kept minimal to avoid circular imports between
``routes.py`` (Handler class) and ``_handlers.py`` (POST handler functions)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any


def _route_state(handler: BaseHTTPRequestHandler) -> Any:
    """Retrieve SparkWebState from the server instance."""
    return getattr(handler.server, "state", None)
