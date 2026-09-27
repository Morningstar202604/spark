"""最近工作目录测试（体验增强）：去重、上限、持久化。"""

from __future__ import annotations

from pathlib import Path

# 隔离说明：recent_dirs 现于调用时动态取 config_dir()（读 SPARK2_HOME），
# tests/conftest.py 的 autouse fixture 已把 SPARK2_HOME 指到 tmp_path，无需再 monkeypatch。


def test_remember_and_load(tmp_path: Path) -> None:
    from spark2 import recent_dirs as rd

    rd.remember("/proj/a")
    rd.remember("/proj/b")
    assert rd.load_recent() == ["/proj/b", "/proj/a"]


def test_dedup_and_max(tmp_path: Path) -> None:
    from spark2 import recent_dirs as rd

    for i in range(12):
        rd.remember(f"/proj/p{i}")
    recent = rd.load_recent()
    assert len(recent) == rd.MAX_RECENT  # 上限 8
    assert recent[0] == "/proj/p11"  # 最新在前
    # 重复访问提到最前
    rd.remember("/proj/p5")
    recent = rd.load_recent()
    assert recent[0] == "/proj/p5"
    assert recent.count("/proj/p5") == 1


def test_empty_ignored(tmp_path: Path) -> None:
    from spark2 import recent_dirs as rd

    rd.remember("   ")
    assert rd.load_recent() == []
