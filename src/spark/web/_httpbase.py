"""The request-handler contract shared by ``routes`` and ``_handlers``.

``spark.web._handlers`` holds free functions that take the live handler as their
first argument.  They were annotated with ``BaseHTTPRequestHandler`` while
calling ``handler._json(...)``, which only exists on the ``Handler`` class built
inside ``routes.build_handler_class`` — that mismatch accounted for 61 of the
project's mypy errors.  Declaring the helpers once, on a base class that
``routes.Handler`` actually inherits, gives both sides the same nominal type.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler


class HandlerBase(BaseHTTPRequestHandler):
    """``BaseHTTPRequestHandler`` plus the response helpers every route uses."""

    def _json(self, code: int, body: dict | list) -> None:
        raise NotImplementedError

    def _html(self, request_path: str = "/") -> None:
        raise NotImplementedError

    def _read_json(self) -> dict:
        raise NotImplementedError
