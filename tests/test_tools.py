from pathlib import Path

from spark.sandbox import WorkdirSandbox
from spark.tools.fs import ApplyPatchArgs, ListDirArgs, ReadFileArgs, WriteFileArgs, apply_patch, list_dir, read_file, write_file
from spark.tools.shell import RunShellArgs, run_shell


def test_read_write_list(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    result = write_file(box, WriteFileArgs(path="a.txt", content="hello\nworld\n"))
    assert result.ok
    listed = list_dir(box, ListDirArgs(path="."))
    assert listed.ok
    names = [e["name"] for e in listed.payload["entries"]]
    assert "a.txt" in names
    read = read_file(box, ReadFileArgs(path="a.txt", offset=2, limit=1))
    assert read.payload["content"] == "world"


def test_path_traversal_rejected(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    result = read_file(box, ReadFileArgs(path="../outside.txt"))
    assert result.ok is False
    assert "escapes" in result.payload["error"]


def test_apply_patch_unique(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    write_file(box, WriteFileArgs(path="b.txt", content="foo bar foo"))
    ok = apply_patch(box, ApplyPatchArgs(path="b.txt", old_text="bar", new_text="baz"))
    assert ok.ok
    assert (tmp_path / "b.txt").read_text() == "foo baz foo"


def test_apply_patch_zero_and_many(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    write_file(box, WriteFileArgs(path="c.txt", content="aa aa"))
    zero = apply_patch(box, ApplyPatchArgs(path="c.txt", old_text="zz", new_text="yy"))
    assert zero.ok is False
    many = apply_patch(box, ApplyPatchArgs(path="c.txt", old_text="aa", new_text="bb"))
    assert many.ok is False
    assert (tmp_path / "c.txt").read_text() == "aa aa"


def test_shell_timeout(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    result = run_shell(box, RunShellArgs(command="sleep 2"), timeout_sec=1, max_output_chars=100)
    assert result.ok is False
    assert result.payload["error"] == "timeout"
