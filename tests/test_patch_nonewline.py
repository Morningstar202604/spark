"""unified diff 的 "\\ No newline at end of file" 标记支持。

标准语义：标记跟在 '+' 行后 = 新文件无尾换行；跟在 '-' 或上下文行后 =
旧文件无尾换行（上下文行时两侧同时生效）。字节级断言。
"""

from __future__ import annotations

from pathlib import Path

from spark2.patch_apply import apply_patch

NL_MARK = "\\ No newline at end of file"


def test_marker_new_side_drops_trailing_newline(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_bytes(b"a\nb\n")
    patch = f"--- a/x.txt\n+++ b/x.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+b2\n{NL_MARK}\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "x.txt").read_bytes() == b"a\nb2"


def test_marker_old_side_gains_trailing_newline(tmp_path: Path) -> None:
    (tmp_path / "y.txt").write_bytes(b"a\nb")
    patch = f"--- a/y.txt\n+++ b/y.txt\n@@ -1,2 +1,2 @@\n a\n-b\n{NL_MARK}\n+b2\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "y.txt").read_bytes() == b"a\nb2\n"


def test_marker_both_sides(tmp_path: Path) -> None:
    (tmp_path / "z.txt").write_bytes(b"a\nb")
    patch = f"--- a/z.txt\n+++ b/z.txt\n@@ -1,2 +1,2 @@\n a\n-b\n{NL_MARK}\n+b2\n{NL_MARK}\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "z.txt").read_bytes() == b"a\nb2"


def test_marker_new_file_without_trailing_newline(tmp_path: Path) -> None:
    patch = f"--- a/n.txt\n+++ b/n.txt\n@@ -0,0 +1,2 @@\n+# t\n+body\n{NL_MARK}\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "n.txt").read_bytes() == b"# t\nbody"


def test_marker_context_line_both_sides(tmp_path: Path) -> None:
    """上下文行后的标记 = 两侧文件都以无尾换行结束。"""
    (tmp_path / "c.txt").write_bytes(b"a\nb")
    patch = f"--- a/c.txt\n+++ b/c.txt\n@@ -1,2 +1,2 @@\n-a\n+a2\n b\n{NL_MARK}\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "c.txt").read_bytes() == b"a2\nb"
