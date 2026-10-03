"""pytest 公共配置：每个测试隔离配置目录，避免写坏真实 ~/.spark。"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path):
    old = os.environ.get("SPARK_HOME")
    os.environ["SPARK_HOME"] = str(tmp_path / ".spark-home")
    yield
    if old is None:
        os.environ.pop("SPARK_HOME", None)
    else:
        os.environ["SPARK_HOME"] = old
