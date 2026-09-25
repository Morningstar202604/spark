"""文件工具测试：读写、diff 预览、列表、搜索。"""
from __future__ import annotations

from pathlib import Path

from spark2.tools.base import ToolContext
from spark2.tools.fs import build_file_tools, read_file, search, write_file

REGS = {t.name: t for t in build_file_tools()}


def _ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workdir=tmp_path)


async def test_write_and_read(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    out = await write_file({"path": "a.txt", "content": "line1\nline2\n"}, ctx)
    assert "已写入" in out
    assert (tmp_path / "a.txt").read_text() == "line1\nline2\n"
    out2 = await read_file({"path": "a.txt"}, ctx)
    assert "line1" in out2 and "line2" in out2


async def test_write_preview_shows_diff(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (tmp_path / "a.txt").write_text("old line\n", encoding="utf-8")
    summary, diff = REGS["write_file"].preview({"path": "a.txt", "content": "new line\n"}, ctx)
    assert "写入" in summary
    assert "-old line" in diff
    assert "+new line" in diff


async def test_list_dir(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (tmp_path / "x.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out = await REGS["list_dir"].handler({"path": "."}, ctx)
    assert "x.py" in out and "[d] sub" in out


async def test_search_with_rg(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    (tmp_path / "main.py").write_text("def hello_world():\n    pass\n", encoding="utf-8")
    out = await search({"query": "hello_world", "path": "."}, ctx)
    assert "main.py" in out


async def test_read_missing_file(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    out = await read_file({"path": "nope.txt"}, ctx)
    assert "不存在" in out
