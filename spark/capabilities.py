"""软依赖能力探针：启动一次性探测，结果缓存。

避免各处 try/except ImportError 散落。所有"可选能力"在此统一判断。
"""
from __future__ import annotations

import importlib.util
import shutil

# ---------------------------------------------------------------------------
# 启动时一次性 probe（import 即计算，后续只读缓存）
# ---------------------------------------------------------------------------

tiktoken_available: bool = importlib.util.find_spec("tiktoken") is not None
openai_available: bool = importlib.util.find_spec("openai") is not None
trafilatura_available: bool = importlib.util.find_spec("trafilatura") is not None
mcp_available: bool = importlib.util.find_spec("mcp") is not None
sentence_transformers_available: bool = (
    importlib.util.find_spec("sentence_transformers") is not None
)
tree_sitter_available: bool = importlib.util.find_spec("tree_sitter") is not None

# 办公文档
docx_available: bool = importlib.util.find_spec("docx") is not None
openpyxl_available: bool = importlib.util.find_spec("openpyxl") is not None
pypdf_available: bool = importlib.util.find_spec("pypdf") is not None

# CLI / TTY
textual_available: bool = importlib.util.find_spec("textual") is not None
node_available: bool = shutil.which("node") is not None


def describe() -> dict:
    """返回能力字典（前端/日志用）。"""
    return {
        "core": {
            "openai_sdk": openai_available,
            "tiktoken": tiktoken_available,
            "trafilatura": trafilatura_available,
            "mcp": mcp_available,
            "tree_sitter": tree_sitter_available,
        },
        "office": {
            "docx": docx_available,
            "xlsx": openpyxl_available,
            "pdf": pypdf_available,
        },
        "memory": {
            "sentence_transformers": sentence_transformers_available,
        },
        "cli": {
            "textual": textual_available,
            "node": node_available,
        },
    }
