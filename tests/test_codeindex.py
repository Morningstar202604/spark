"""代码索引测试（P3 ⑤）：AST 符号 / 行级符号 / 搜索 / 语法诊断。"""
from __future__ import annotations

from pathlib import Path

from spark2 import codeindex as ci


def _make_proj(tmp_path: Path) -> Path:
    (tmp_path / "lib.py").write_text(
        "def add(a, b):\n    return a + b\n\n\nclass Calc:\n    def mul(self, a, b):\n        return a * b\n\n\nCONST = 42\n",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text(
        "export function greet(name) { return 'hi ' + name; }\nconst port = 3000;\nclass Server {}\n",
        encoding="utf-8",
    )
    (tmp_path / "main.go").write_text(
        "package main\nfunc main() {}\ntype User struct { Name string }\n",
        encoding="utf-8",
    )
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "junk.py").write_text("x = 1\n", encoding="utf-8")  # skip 不在排除列表？在！
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "a.py").write_text("y = 2\n", encoding="utf-8")  # 应排除
    return tmp_path


def test_index_python_ast_symbols(tmp_path: Path) -> None:
    idx = ci.index_project(str(_make_proj(tmp_path)), use_cache=False)
    names = {s["name"] for s in idx["symbols"]}
    assert {"add", "Calc", "mul"} <= names
    func = next(s for s in idx["symbols"] if s["name"] == "add")
    assert func["kind"] == "function" and func["line"] == 1 and func["file"] == "lib.py"
    m = next(s for s in idx["symbols"] if s["name"] == "mul")
    assert m["kind"] == "method"
    # 排除目录
    assert not any("node_modules" in s["file"] for s in idx["symbols"])
    assert "lib.py" in idx["files"] and "app.js" in idx["files"] and "main.go" in idx["files"]


def test_index_cache_fresh(tmp_path: Path) -> None:
    proj = _make_proj(tmp_path)
    idx1 = ci.index_project(str(proj), use_cache=True)
    idx2 = ci.index_project(str(proj), use_cache=True)  # 命中缓存
    assert idx2["generated_at"] == idx1["generated_at"]
    # 改文件后缓存失效（符号集合变化）
    (proj / "lib.py").write_text("def new_fn():\n    pass\n", encoding="utf-8")
    idx3 = ci.index_project(str(proj), use_cache=True)
    names3 = {s["name"] for s in idx3["symbols"]}
    assert "new_fn" in names3
    assert "add" not in names3


def test_search_symbol(tmp_path: Path) -> None:
    idx = ci.index_project(str(_make_proj(tmp_path)), use_cache=False)
    hits = ci.search_symbol(idx, "add")
    assert hits and hits[0]["name"] == "add"
    hits2 = ci.search_symbol(idx, "zzz")
    assert hits2 == []


def test_lint_python_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n    pass\n", encoding="utf-8")
    diags = ci.lint_file(str(tmp_path), str(bad))
    assert diags and diags[0]["severity"] == "error"


def test_lint_python_ok(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text("def ok():\n    return 1\n", encoding="utf-8")
    assert ci.lint_file(str(tmp_path), str(good)) == []


def test_lint_js_node_check(tmp_path: Path) -> None:
    bad = tmp_path / "bad.js"
    bad.write_text("const x = ;\n", encoding="utf-8")
    diags = ci.lint_file(str(tmp_path), str(bad))
    assert diags and diags[0]["severity"] == "error"
    good = tmp_path / "good.js"
    good.write_text("const y = 1;\n", encoding="utf-8")
    assert ci.lint_file(str(tmp_path), str(good)) == []
