"""轻量代码索引 + 语法诊断（P3 ⑤）。

设计取舍（轻量、不重复造轮子）：
- Python 用标准库 ast 精确提取符号（函数/类/方法/常量），零依赖；
- 其他常见语言（js/ts/java/go/rs/c/cpp/h）用行级正则提取顶层符号（够用、不引 tree-sitter）；
- 语法诊断：.py 用 py_compile（进程内秒级）；.js/.mjs/.cjs 用 node --check（node 现役可用）；
- 索引缓存到 ~/.spark2/codeindex/<workdir_hash>.json，按文件 mtime 增量失效，不污染用户项目目录。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from spark2.config import CONFIG_DIR

INDEX_EXT = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".jsx": "javascript", ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
}
EXCLUDE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "target",
    "__pycache__", ".pytest_cache", ".mypy_cache", "out", ".idea", ".vscode",
}
_SKIP_SIZE = 1_000_000  # 单文件超 1MB 不索引（避免误入大文件）

# 行级正则：{语言: [(pattern, kind)]}——只匹配"顶层声明"风格的简单模式
_LINE_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "javascript": [
        (r"\b(?:export\s+)?(?:async\s+)?function\s+\*?\s*([A-Za-z_$][\w$]*)\s*\(", "function"),
        (r"\b(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", "class"),
        (r"\b(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|\()", "function"),
        (r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", "variable"),
    ],
    "typescript": [
        (r"\b(?:export\s+)?(?:async\s+)?function\s+\*?\s*([A-Za-z_$][\w$]*)\s*\(", "function"),
        (r"\b(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", "class"),
        (r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", "variable"),
        (r"\b(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)", "interface"),
        (r"\b(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*=", "type"),
        (r"\b(?:export\s+)?enum\s+([A-Za-z_$][\w$]*)", "enum"),
    ],
    "java": [
        (r"\b(?:public|private|protected|static|final|abstract|synchronized|\s)*\s+([A-Za-z_][\w<>?,\s]*)\s+([A-Za-z_][\w]*)\s*\(", "function"),
        (r"\b(?:public|abstract|final)?\s*class\s+([A-Za-z_][\w]*)", "class"),
        (r"\b(?:public|abstract|final)?\s*interface\s+([A-Za-z_][\w]*)", "interface"),
        (r"\b(?:public|abstract|final)?\s*enum\s+([A-Za-z_][\w]*)", "enum"),
    ],
    "go": [
        (r"\bfunc\s+\([^)]*\)\s+([A-Za-z_][\w]*)", "method"),
        (r"\bfunc\s+([A-Za-z_][\w]*)\s*\(", "function"),
        (r"\btype\s+([A-Za-z_][\w]*)\s+struct\s*\{", "struct"),
        (r"\btype\s+([A-Za-z_][\w]*)\s+interface\s*\{", "interface"),
    ],
    "rust": [
        (r"\bfn\s+([A-Za-z_][\w]*)\s*\(", "function"),
        (r"\bstruct\s+([A-Za-z_][\w]*)", "struct"),
        (r"\benum\s+([A-Za-z_][\w]*)", "enum"),
        (r"\btrait\s+([A-Za-z_][\w]*)", "trait"),
        (r"\bimpl\s+([A-Za-z_][\w]*)", "impl"),
    ],
    "c": [
        (r"\b(?:static\s+)?(?:inline\s+)?[\w\s\*]+?\b([A-Za-z_][\w]*)\s*\([^;]*\)\s*\{", "function"),
        (r"\b(?:typedef\s+)?(?:struct|union|enum)\s+([A-Za-z_][\w]*)", "type"),
    ],
    "cpp": [
        (r"\b(?:static\s+)?(?:inline\s+)?(?:[\w:<>,\s\*&]+)\s+([A-Za-z_][\w]*)\s*\([^;]*\)\s*\{", "function"),
        (r"\bclass\s+([A-Za-z_][\w]*)", "class"),
        (r"\b(?:typedef\s+)?(?:struct|union|enum)\s+([A-Za-z_][\w]*)", "type"),
    ],
}


def _workdir_key(workdir: str) -> str:
    return hashlib.sha1(workdir.encode("utf-8")).hexdigest()[:12]


def index_cache_path(workdir: str) -> Path:
    return CONFIG_DIR / "codeindex" / f"{_workdir_key(workdir)}.json"


def _py_symbols(text: str, rel_path: str) -> list[dict]:
    """用标准库 ast 精确提取 Python 符号。"""
    out: list[dict] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(node, ast.AsyncFunctionDef):
                kind = "async-function"
            elif _inside_class(tree, node):
                kind = "method"
            else:
                kind = "function"
            args = [a.arg for a in node.args.args[:4]]
            out.append({
                "kind": kind, "name": node.name, "line": node.lineno,
                "file": rel_path, "args": ", ".join(args) + ("…" if len(node.args.args) > 4 else ""),
            })
        elif isinstance(node, ast.ClassDef):
            out.append({"kind": "class", "name": node.name, "line": node.lineno, "file": rel_path, "args": ""})
    return out


def _inside_class(tree: ast.AST, node: ast.AST) -> bool:
    for parent in ast.walk(tree):
        if isinstance(parent, ast.ClassDef):
            for child in ast.iter_child_nodes(parent):
                if child is node:
                    return True
    return False


def _regex_symbols(text: str, rel_path: str, lang: str) -> list[dict]:
    out: list[dict] = []
    for pat, kind in _LINE_PATTERNS.get(lang, []):
        for m in re.finditer(pat, text, re.M):
            name = m.group(1) if m.lastindex and m.lastindex >= 1 else ""
            if not name:
                continue
            line = text.count("\n", 0, m.start()) + 1
            out.append({"kind": kind, "name": name, "line": line, "file": rel_path, "args": ""})
    # 去重（同名同行同 kind）
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for s in out:
        key = (s["file"], s["name"], s["line"], s["kind"])
        if key not in seen:
            seen.add(key)
            uniq.append(s)
    return uniq


def index_project(workdir: str, use_cache: bool = True) -> dict:
    """扫描工作目录生成符号索引，返回 {files, symbols, language_count, generated_at}。"""
    root = Path(workdir)
    cache_p = index_cache_path(workdir)
    if use_cache and cache_p.exists():
        try:
            cached = json.loads(cache_p.read_text(encoding="utf-8"))
            if _cache_fresh(cached, root):
                return cached
        except Exception:  # noqa: BLE001
            pass

    symbols: list[dict] = []
    files: dict[str, dict] = {}
    lang_count: dict[str, int] = {}

    def walk(d: Path) -> None:
        for entry in sorted(d.iterdir()):
            if entry.is_dir():
                if entry.name in EXCLUDE_DIRS or entry.name.startswith("."):
                    continue
                walk(entry)
            elif entry.is_file() and entry.stat().st_size <= _SKIP_SIZE:
                ext = entry.suffix.lower()
                lang = INDEX_EXT.get(ext)
                if not lang:
                    continue
                rel = str(entry.relative_to(root))
                try:
                    text = entry.read_text(encoding="utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
                mtime = entry.stat().st_mtime
                if lang == "python":
                    syms = _py_symbols(text, rel)
                else:
                    syms = _regex_symbols(text, rel, lang)
                symbols.extend(syms)
                files[rel] = {"mtime": mtime, "size": len(text.encode("utf-8")), "lang": lang}
                lang_count[lang] = lang_count.get(lang, 0) + 1

    if root.exists():
        walk(root)

    result = {
        "files": files,
        "symbols": symbols,
        "language_count": lang_count,
        "generated_at": __import__("time").time(),
        "workdir": workdir,
    }
    cache_p.parent.mkdir(parents=True, exist_ok=True)
    cache_p.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def _cache_fresh(cached: dict, root: Path) -> bool:
    files = cached.get("files") or {}
    for rel, meta in files.items():
        p = root / rel
        if not p.exists():
            return False
        try:
            st = p.stat()
            # mtime（秒级精度）+ 文件大小双因子：同秒改写时靠 size 兜底
            if abs(st.st_mtime - float(meta.get("mtime", 0))) > 1:
                return False
            if st.st_size != int(meta.get("size", -1)):
                return False
        except OSError:
            return False
    return True


def search_symbol(index: dict, query: str, limit: int = 20) -> list[dict]:
    """在索引里模糊搜索符号（名称包含 / 前缀匹配），按行号排序。"""
    q = query.lower().strip()
    if not q or not index:
        return []
    hits = []
    for s in index.get("symbols", []):
        name = str(s.get("name", "")).lower()
        if q in name or name.startswith(q):
            hits.append(s)
    hits.sort(key=lambda x: (x.get("file", ""), int(x.get("line", 0))))
    return hits[:limit]


def lint_file(workdir: str, path: str) -> list[dict]:
    """单文件语法诊断：.py → py_compile；.js/.mjs/.cjs → node --check。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path(workdir) / p
    p = p.resolve()
    if not p.exists() or p.stat().st_size > _SKIP_SIZE:
        return [{"severity": "error", "message": f"文件不存在或过大：{path}"}]
    ext = p.suffix.lower()
    if ext == ".py":
        try:
            import py_compile
            py_compile.compile(str(p), doraise=True)
            return []
        except py_compile.PyCompileError as e:
            return [{"severity": "error", "message": str(e).split("\n", 1)[0]}]
    if ext in (".js", ".mjs", ".cjs"):
        try:
            r = subprocess.run(
                ["node", "--check", str(p)], capture_output=True, text=True, timeout=10
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return [{"severity": "warning", "message": "无法运行 node --check（超时或未安装）"}]
        if r.returncode != 0:
            return [{"severity": "error", "message": (r.stderr or r.stdout).strip().split("\n", 1)[0]}]
        return []
    return [{"severity": "warning", "message": "暂不支持该语言语法诊断"}]
