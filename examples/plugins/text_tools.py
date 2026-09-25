"""示例插件 2：slugify / base64 工具（register 写法）。"""
from spark2.tools.base import Tool
import base64
import re


def _slugify(args, ctx):
    s = str(args.get("text") or "")
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", s.strip().lower()).strip("-")
    return s or "(空)"


def _b64(args, ctx):
    s = str(args.get("text") or "")
    mode = str(args.get("mode") or "encode")
    try:
        if mode == "encode":
            return base64.b64encode(s.encode("utf-8")).decode("ascii")
        return base64.b64decode(s).decode("utf-8")
    except Exception as e:
        return f"base64 处理失败：{e}"


def register(reg):
    reg.append(Tool(
        name="slugify", description="把标题转成 URL 友好的 slug（空格/中文/符号 → -）",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        category="read",
        handler=_slugify,
    ))
    reg.append(Tool(
        name="base64", description="base64 编解码（mode=encode/decode）",
        parameters={"type": "object", "properties": {"text": {"type": "string"}, "mode": {"type": "string", "enum": ["encode", "decode"], "default": "encode"}}, "required": ["text"]},
        category="read",
        handler=_b64,
    ))
