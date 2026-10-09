"""apply_patch 工具与底层 unified diff 解析/校验/应用测试。

覆盖：多文件解析、增删交错应用、新文件创建、越界/受保护拒绝、
上下文不匹配整体拒绝、畸形补丁报错、工具注册与 preview。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from spark.approval import ApprovalGate
from spark.patch_apply import (
    PatchError,
    apply_patch,
    apply_to_text,
    parse_patch,
)
from spark.tools import build_registry


def _multi_patch() -> str:
    return """--- a/a.py
+++ a/a.py
@@ -1,2 +1,2 @@
 x = 1
-print(x)
+print(x + 1)
--- a/b.py
+++ b/b.py
@@ -1,2 +1,3 @@
 def old():
-    pass
+    return 42
+    pass
"""


def test_parse_multi_file_and_counts(tmp_path: Path):
    files = parse_patch(_multi_patch(), tmp_path, [])
    assert [f.path for f in files] == ["a/a.py", "a/b.py"]
    assert files[0].added == 1 and files[0].removed == 1
    assert files[1].added == 2 and files[1].removed == 1


def test_apply_multi_file(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\nprint(x)\n")
    (tmp_path / "b.py").write_text("def old():\n    pass\n")
    result, changes = apply_patch(_multi_patch(), tmp_path, [])
    assert "2 个文件" in result and "+3/-2" in result
    # 源文件以 \n 结尾 → 补丁后必须保留尾换行（unified diff 语义，见 test_platform_fixes）
    assert (tmp_path / "a.py").read_text() == "x = 1\nprint(x + 1)\n"
    assert (tmp_path / "b.py").read_text() == "def old():\n    return 42\n    pass\n"
    assert len(changes) == 2


def test_apply_patch_only_filters_files(tmp_path: Path):
    """逐文件审批：only 只应用勾选的文件，其余跳过。"""
    (tmp_path / "a.py").write_text("x = 1\nprint(x)\n")
    (tmp_path / "b.py").write_text("def old():\n    pass\n")
    result, changes = apply_patch(_multi_patch(), tmp_path, [], only=["a.py"])
    assert (tmp_path / "a.py").read_text() == "x = 1\nprint(x + 1)\n"
    assert (tmp_path / "b.py").read_text() == "def old():\n    pass\n"  # 未选中，不变
    assert len(changes) == 1 and changes[0]["path"] == "a.py"


def test_apply_patch_only_empty_skips_all(tmp_path: Path):
    """逐文件审批：勾选列表为空 → 全部跳过，不落盘。"""
    (tmp_path / "a.py").write_text("x = 1\nprint(x)\n")
    result, changes = apply_patch(_multi_patch(), tmp_path, [], only=[])
    assert "没有勾选任何文件" in result
    assert (tmp_path / "a.py").read_text() == "x = 1\nprint(x)\n"
    assert changes == []


def test_interleaved_add_remove(tmp_path: Path):
    """同一 hunk 内多处增删要按位置交错，不能统一插到末尾。"""
    src = "a\nb\nc\nd\ne\n"
    patch = """--- a/f.txt
+++ b/f.txt
@@ -1,5 +1,5 @@
 a
-b
+b1
 c
-d
+d1
 e
"""
    (tmp_path / "f.txt").write_text(src)
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "f.txt").read_text() == "a\nb1\nc\nd1\ne\n"


def test_create_new_file(tmp_path: Path):
    patch = """--- a/notes.md
+++ b/notes.md
@@ -0,0 +1,3 @@
+# 标题
+正文第一行
+正文第二行
"""
    result, _ = apply_patch(patch, tmp_path, [])
    assert (tmp_path / "notes.md").read_text() == "# 标题\n正文第一行\n正文第二行\n"
    assert "1 个文件" in result


def test_multi_hunk_same_file(tmp_path: Path):
    src = "l1\nl2\nl3\nl4\nl5\n"
    patch = """--- a/g.txt
+++ b/g.txt
@@ -1,2 +1,2 @@
 l1
-l2
+l2x
@@ -4,2 +4,2 @@
 l4
-l5
+l5x
"""
    (tmp_path / "g.txt").write_text(src)
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "g.txt").read_text() == "l1\nl2x\nl3\nl4\nl5x\n"


def test_reject_escape(tmp_path: Path):
    patch = "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises(PatchError, match="工作目录之外"):
        apply_patch(patch, tmp_path, [])


def test_reject_mixed_escape_after_valid(tmp_path: Path):
    """前一段合法、后一段越界：整体拒绝且不写任何文件。"""
    (tmp_path / "ok.txt").write_text("x\n")
    patch = (
        "--- a/ok.txt\n+++ b/ok.txt\n@@ -1 +1 @@\n-x\n+1\n"
        "--- a/../../etc/passwd\n+++ b/../../etc/passwd\n@@ -1 +1 @@\n-old\n+new\n"
    )
    with pytest.raises(PatchError, match="工作目录之外"):
        apply_patch(patch, tmp_path, [])
    assert (tmp_path / "ok.txt").read_text() == "x\n"  # 未落地


def test_reject_protected(tmp_path: Path):
    (tmp_path / "secret.txt").write_text("s\n")
    patch = "--- a/secret.txt\n+++ b/secret.txt\n@@ -1 +1 @@\n-s\n+x\n"
    with pytest.raises(PatchError, match="受保护路径"):
        apply_patch(patch, tmp_path, [tmp_path / "secret.txt"])


def test_reject_context_mismatch(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\nprint(x)\n")
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n WRONG\n-print(x)\n+print(x+1)\n"
    with pytest.raises(PatchError, match="上下文不匹配"):
        apply_patch(patch, tmp_path, [])
    assert (tmp_path / "a.py").read_text() == "x = 1\nprint(x)\n"  # 未改动


def test_context_mismatch_error_helps_model_self_correct(tmp_path: Path):
    """上下文对不上时，错误里要带文件真实邻域与行号，模型才能一次改对。"""
    body = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
    (tmp_path / "a.py").write_text(body, encoding="utf-8")
    # hunk 声称第 5 行是 WRONG，实际第 5 行是 line5
    patch = "--- a/a.py\n+++ b/a.py\n@@ -5,1 +5,1 @@\n-WRONG\n+NEW\n"
    with pytest.raises(PatchError) as exc:
        apply_patch(patch, tmp_path, [])
    msg = str(exc.value)
    assert "'line5'" in msg, msg           # 该位置真实内容
    assert "文件共 10 行" in msg, msg      # 总行数
    assert "line4" in msg and "line6" in msg, msg  # 邻域真实行
    assert "重新生成 hunk" in msg, msg


def test_reject_empty_and_malformed(tmp_path: Path):
    with pytest.raises(PatchError, match="补丁为空"):
        apply_patch("", tmp_path, [])
    with pytest.raises(PatchError, match="没有识别到文件段|无法识别的补丁行"):
        apply_patch("hello world", tmp_path, [])
    with pytest.raises(PatchError, match="没有 @@ hunk|无法识别的补丁行"):
        apply_patch("--- a/x\n+++ b/x\nline\n", tmp_path, [])


def test_apply_to_text_errors():
    _h = type("H", (), {})  # placeholder, 直接用真实 Hunk
    from spark.patch_apply import Hunk

    with pytest.raises(PatchError):
        apply_to_text("a\nb\n", [Hunk(99, 2, 99, 2, [(" ", "a"), ("-", "b")])])


def test_registry_and_preview(tmp_path: Path):
    reg = build_registry()
    assert "apply_patch" in reg
    t = reg["apply_patch"]
    assert t.category == "write"
    (tmp_path / "a.py").write_text("x = 1\nprint(x)\n")
    summary, diff = t.preview(
        {"patch": _multi_patch()},
        type("C", (), {"workdir": tmp_path, "protected": []})(),
    )
    assert "2 个文件" in summary and "+3/-2" in summary
    assert "a/a.py" in diff
    # preview 不落盘
    assert (tmp_path / "b.py").exists() is False


def test_apply_patch_auto_verify_runs_pytest(tmp_path: Path) -> None:
    """改完自动验证：apply_patch 成功后自动跑 pytest 并把结果回填进 tool_result。"""
    from spark.execution import ToolExecutor
    from spark.tools.base import ToolContext
    from spark.tools.patch import build_patch_tool

    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_ok.py").write_text(
        "def test_ok():\n    assert 1 + 1 == 2\n"
    )
    gate = ApprovalGate(mode="auto")
    reg = {t.name: t for t in build_patch_tool()}
    ctx = ToolContext(workdir=tmp_path, protected=[], cancel_event=asyncio.Event(), memory=None, index={})
    ex = ToolExecutor(
        gate=gate,
        registry=reg,
        ctx=ctx,
        cancel_event=asyncio.Event(),
        tool_timeout=60,
        workdir=tmp_path,
        memory=None,
        log_path=None,
        provider_cfg={"model": "mock"},
        auto_verify=True,
    )
    call = {
        "id": "ap1",
        "name": "apply_patch",
        "arguments": {
            "patch": "--- a/new.txt\n+++ a/new.txt\n@@ -0,0 +1,1 @@\n+hello\n"
        },
    }
    msgs: list[dict] = []
    evs = []
    async def run():
        async for ev in ex.execute(msgs, call):
            evs.append(ev)
    asyncio.run(run())
    results = [e for e in evs if e["type"] == "tool_result"]
    assert results
    out = results[0]["output"]
    assert "已应用补丁" in out
    assert "[自动验证]" in out and "1 passed" in out, out


def test_apply_patch_auto_verify_skipped_without_pytest(tmp_path: Path) -> None:
    """无 pytest 配置/无 tests 目录：跳过自动验证，不附加验证文本。"""
    from spark.execution import ToolExecutor
    from spark.tools.base import ToolContext
    from spark.tools.patch import build_patch_tool

    (tmp_path / "plain.txt").write_text("x")
    gate = ApprovalGate(mode="auto")
    reg = {t.name: t for t in build_patch_tool()}
    ctx = ToolContext(workdir=tmp_path, protected=[], cancel_event=asyncio.Event(), memory=None, index={})
    ex = ToolExecutor(
        gate=gate,
        registry=reg,
        ctx=ctx,
        cancel_event=asyncio.Event(),
        tool_timeout=60,
        workdir=tmp_path,
        memory=None,
        log_path=None,
        provider_cfg={"model": "mock"},
        auto_verify=True,
    )
    call = {
        "id": "ap2",
        "name": "apply_patch",
        "arguments": {"files": [{"path": "plain2.txt", "content": "y"}]},
    }
    msgs: list[dict] = []
    evs = []
    async def run():
        async for ev in ex.execute(msgs, call):
            evs.append(ev)
    asyncio.run(run())
    results = [e for e in evs if e["type"] == "tool_result"]
    assert "[自动验证]" not in results[0]["output"]
