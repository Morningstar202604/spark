"""POST handlers 共享助手（_save_config 等跨域复用的最小集合）。"""

from __future__ import annotations


def _save_config(cfg) -> None:
    """Resolve save_config through the server module so that tests can
    monkeypatch ``spark.web.server.save_config`` and intercept calls."""
    import importlib

    importlib.import_module("spark.web.server").save_config(cfg)
