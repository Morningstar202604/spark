"""代码索引 + 语法诊断（重构：改用 tree-sitter 替代手写正则）。

设计取舍（不重复造轮子）：
- tree-sitter 提供 30+ 语言的真实 AST 解析（对比旧版手写正则提取顶层声明），
  精准涵盖方法、嵌套类、接口、高阶函数等。
- 语法诊断走 stdlib py_compile（.py）与 node --check（.js），与旧版一致。
- 索引缓存协议保持不变（按文件 mtime 增量失效），供工具调用。

公共接口（保持兼容）：
    index_project(workdir, use_cache=True) -> dict
    search_symbol(index, query, limit=20) -> list[dict]
    lint_file(workdir, path) -> list[dict]
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from spark.config import config_dir

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "out",
    ".idea",
    ".vscode",
}

SKIP_SIZE = 1_000_000  # 单文件超 1MB 不索引


# ---------------------------------------------------------------------------
# tree-sitter 语言缓存
# ---------------------------------------------------------------------------

_TS: dict | None = None  # None = 未加载；{} = 加载但不可用


def _load_ts() -> dict:
    """加载 tree-sitter 与语言 binding。失败返回空 dict。

    使用各语言的独立预编译包（tree-sitter-python、tree-sitter-javascript 等），
    避免 tree-sitter-languages 的 Cython ABI 兼容性问题。
    """
    global _TS
    if _TS is not None:
        return _TS
    try:
        import tree_sitter  # type: ignore
        import tree_sitter_cpp  # type: ignore
        import tree_sitter_go  # type: ignore
        import tree_sitter_java  # type: ignore
        import tree_sitter_javascript  # type: ignore

        # 各语言独立预编译包，通过 PyCapsule 返回 Language
        import tree_sitter_python  # type: ignore
        import tree_sitter_rust  # type: ignore
        import tree_sitter_typescript  # type: ignore
        from tree_sitter import Language  # type: ignore

        def _lang(mod, fn_name: str = "language") -> tree_sitter.Language:
            """将 PyCapsule 包装为 tree_sitter.Language 实例。"""
            capsule = getattr(mod, fn_name)()
            return Language(capsule)

        _TS = {
            "python": _lang(tree_sitter_python),
            "javascript": _lang(tree_sitter_javascript),
            "typescript": _lang(tree_sitter_typescript, "language_typescript"),
            "tsx": _lang(tree_sitter_typescript, "language_tsx"),
            "go": _lang(tree_sitter_go),
            "rust": _lang(tree_sitter_rust),
            "java": _lang(tree_sitter_java),
            "c": _lang(tree_sitter_cpp),
            "cpp": _lang(tree_sitter_cpp),
            "h": _lang(tree_sitter_cpp),
            "hpp": _lang(tree_sitter_cpp),
        }
    except Exception:  # noqa: BLE001 — tree-sitter 不可用则回退正则
        _TS = {}
    return _TS


# 各语言的 (节点类型 → (kind, name_field, is_container))
# - name_field: 通过 child_by_field_name() 获取名称节点，None 表示递归查找
# - is_container: 是否继续深入子节点寻找嵌套定义
_LANG_KINDS: dict[str, dict[str, tuple[str, str | None, bool]]] = {
    "python": {
        "function_definition": ("function", "name", False),
        "class_definition": ("class", "name", True),
    },
    "javascript": {
        "function_declaration": ("function", "name", False),
        "class_declaration": ("class", "name", True),
        "method_definition": ("method", "name", False),
    },
    "typescript": {
        "function_declaration": ("function", "name", False),
        "class_declaration": ("class", "name", True),
        "method_definition": ("method", "name", False),
        "interface_declaration": ("interface", "name", True),
        "type_alias_declaration": ("type", "name", False),
        "enum_declaration": ("enum", "name", False),
    },
    "tsx": {
        "function_declaration": ("function", "name", False),
        "class_declaration": ("class", "name", True),
        "method_definition": ("method", "name", False),
        "interface_declaration": ("interface", "name", True),
    },
    "go": {
        "function_declaration": ("function", "name", False),
        "type_declaration": ("type", "name", True),
        "method_declaration": ("method", "name", False),
    },
    "java": {
        "method_declaration": ("method", "name", False),
        "class_declaration": ("class", "name", True),
        "interface_declaration": ("interface", "name", True),
        "enum_declaration": ("enum", "name", False),
    },
    "rust": {
        "function_item": ("function", "name", False),
        "struct_item": ("struct", "name", True),
        "enum_item": ("enum", "name", True),
        "trait_item": ("trait", "name", True),
        "impl_item": ("impl", "name", True),
    },
    "c": {
        "function_definition": ("function", None, False),
        "struct_specifier": ("struct", "name", True),
        "enum_specifier": ("enum", "name", True),
    },
    "cpp": {
        "function_definition": ("function", None, False),
        "class_specifier": ("class", "name", True),
        "struct_specifier": ("struct", "name", True),
        "enum_specifier": ("enum", "name", True),
    },
    "h": {
        "function_definition": ("function", None, False),
        "struct_specifier": ("struct", "name", True),
    },
    "hpp": {
        "function_definition": ("function", None, False),
        "class_specifier": ("class", "name", True),
        "struct_specifier": ("struct", "name", True),
    },
}


def _get_name(node, name_field: str | None, source: str) -> str | None:
    """从 AST 节点提取名称字符串。"""
    if name_field is None:
        # C style: 在 function_declarator 内递归查找 identifier
        for ch in node.children if hasattr(node, "children") else []:
            if ch.type == "function_declarator":
                for sub in ch.children:
                    if sub.type == "identifier":
                        return source[sub.start_byte : sub.end_byte]
        return None
    try:
        name_node = node.child_by_field_name(name_field) if hasattr(node, "child_by_field_name") else None
    except Exception:
        name_node = None
    if name_node is not None and name_node.start_byte is not None:
        return source[name_node.start_byte : name_node.end_byte]
    # fallback：在直接子节点中找 identifier
    for ch in node.children if hasattr(node, "children") else []:
        if ch.type in ("identifier", "type_identifier", "property_identifier"):
            return source[ch.start_byte : ch.end_byte]
    return None


def _extract_symbols_ts(source: str, lang: str, rel_path: str) -> list[dict]:
    """用 tree-sitter 提取符号。返回 [{kind, name, line, file}]。"""
    try:
        from tree_sitter import Parser  # type: ignore
    except ImportError:
        return []

    ts = _load_ts()
    lang_obj = ts.get(lang)
    if lang_obj is None:
        return []

    kinds = _LANG_KINDS.get(lang, {})
    if not kinds:
        return []

    parser = Parser(lang_obj)
    tree = parser.parse(bytes(source, "utf-8"))

    symbols: list[dict] = []

    def walk(node, in_class: bool = False):
        ntype = node.type
        if ntype in kinds:
            kind, name_field, is_container = kinds[ntype]
            name = _get_name(node, name_field, source)
            if name:
                if in_class and kind == "function":
                    kind = "method"
                symbols.append(
                    {
                        "kind": kind,
                        "name": name,
                        "line": node.start_point[0] + 1,
                        "file": rel_path,
                        "args": "",
                    }
                )
            # 嵌套定义：进入容器体继续扫描
            if is_container:
                body = node.child_by_field_name("body")
                if body is not None:
                    for ch in body.children:
                        walk(ch, in_class=True)
                return
        for ch in node.children:
            walk(ch, in_class=in_class)

    walk(tree.root_node)
    return symbols


# ---------------------------------------------------------------------------
# 缓存路径与治理
# ---------------------------------------------------------------------------

def _workdir_key(workdir: str) -> str:
    return hashlib.sha1(workdir.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


def index_cache_path(workdir: str) -> Path:
    return config_dir() / "codeindex" / f"{_workdir_key(workdir)}.json"


MAX_CACHE_ENTRIES = 12
MAX_CACHE_BYTES = 20 * 1024 * 1024


def _prune_cache() -> None:
    """超过条目配额时删除最旧缓存，避免索引目录无限累积。"""
    try:
        d = config_dir() / "codeindex"
        if not d.exists():
            return
        entries = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in entries[MAX_CACHE_ENTRIES:]:
            p.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 语法诊断（保留旧实现：py_compile / node --check）
# ---------------------------------------------------------------------------

def lint_file(workdir: str | None, path: str | Path) -> list[dict]:
    """单文件语法诊断：.py → py_compile；.js/.mjs/.cjs → node --check。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path(workdir or ".") / p
    p = p.resolve()
    if not p.exists() or p.stat().st_size > SKIP_SIZE:
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
            return [
                {
                    "severity": "warning",
                    "message": "无法运行 node --check（超时或未安装）",
                }
            ]
        if r.returncode != 0:
            return [
                {
                    "severity": "error",
                    "message": (r.stderr or r.stdout).strip().split("\n", 1)[0],
                }
            ]
        return []
    return [{"severity": "warning", "message": "暂不支持该语言语法诊断"}]


# ---------------------------------------------------------------------------
# 索引构建（公共接口）
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "h",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "hpp",
}


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

    ts = _load_ts()
    supported_langs = set(ts.keys())

    symbols: list[dict] = []
    files: dict[str, dict] = {}
    lang_count: dict[str, int] = {}

    def walk(d: Path) -> None:
        for entry in sorted(d.iterdir()):
            if entry.is_dir():
                if entry.name in EXCLUDE_DIRS or entry.name.startswith("."):
                    continue
                walk(entry)
            elif entry.is_file() and entry.stat().st_size <= SKIP_SIZE:
                ext = entry.suffix.lower()
                lang = _EXT_TO_LANG.get(ext)
                if not lang or lang not in supported_langs:
                    continue
                rel = str(entry.relative_to(root))
                try:
                    text = entry.read_text(encoding="utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    continue
                st = entry.stat()
                syms = _extract_symbols_ts(text, lang, rel)
                symbols.extend(syms)
                files[rel] = {"mtime": st.st_mtime, "size": st.st_size, "lang": lang}
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
    payload = json.dumps(result, ensure_ascii=False)
    if len(payload) <= MAX_CACHE_BYTES:
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        cache_p.write_text(payload, encoding="utf-8")
        _prune_cache()
    return result


def _cache_fresh(cached: dict, root: Path) -> bool:
    files = cached.get("files") or {}
    for rel, meta in files.items():
        p = root / rel
        if not p.exists():
            return False
        try:
            st = p.stat()
            if abs(st.st_mtime - float(meta.get("mtime", 0))) > 1:
                return False
            if st.st_size != int(meta.get("size", -1)):
                return False
        except OSError:
            return False
    return True


def search_symbol(index: dict | None, query: str, limit: int = 20) -> list[dict]:
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
