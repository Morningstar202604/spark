"""pytest 公共配置：每个测试隔离配置目录，避免写坏真实 ~/.spark2。"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path):
    old = os.environ.get("SPARK2_HOME")
    os.environ["SPARK2_HOME"] = str(tmp_path / ".spark2-home")
    yield
    if old is None:
        os.environ.pop("SPARK2_HOME", None)
    else:
        os.environ["SPARK2_HOME"] = old
