"""pytest 公共配置：每个测试隔离配置目录，避免写坏真实 ~/.spark。"""
from __future__ import annotations

import os

import pytest


def _reset_checkpointer() -> None:
    try:
        from spark.core.turn_store import reset_persistent_checkpointer

        reset_persistent_checkpointer()
    except Exception:  # pragma: no cover - optional import path
        pass


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path):
    old = os.environ.get("SPARK_HOME")
    os.environ["SPARK_HOME"] = str(tmp_path / ".spark-home")
    # The durable turn checkpointer is process-wide: one opened against the
    # previous test's home would keep its SQLite connection (bound to the
    # shared runtime loop) alive into the next test.
    _reset_checkpointer()
    yield
    _reset_checkpointer()
    if old is None:
        os.environ.pop("SPARK_HOME", None)
    else:
        os.environ["SPARK_HOME"] = old
