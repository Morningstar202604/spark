"""斜杠命令/快捷指令：把常用工作流封装为预设 prompt。

对标 Claude Code slash commands / Cursor 快捷指令。
用户输入以 "/" 开头的消息时，匹配到预设指令则展开为完整 prompt 再发给模型。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SlashCommand:
    name: str
    label: str
    description: str
    template: str  # 支持 {workdir} {user_input} 占位符


SLASH_COMMANDS: list[SlashCommand] = [
    SlashCommand(
        name="explain",
        label="解释代码",
        description="解释选中/当前文件的代码逻辑",
        template=(
            "请详细解释这个代码的作用、设计思路、关键变量和潜在问题。\n"
            "如果 {user_input} 提供了具体文件/函数，聚焦那个部分。\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="refactor",
        label="重构",
        description="重构当前文件/模块，保持行为不变",
        template=(
            "请重构以下代码：\n"
            "1. 先读相关文件，理解当前实现\n"
            "2. 识别坏味道（长函数、重复、高耦合、命名不清）\n"
            "3. 给出重构计划（update_plan），逐步执行\n"
            "4. 改完后跑测试验证行为不变\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="test",
        label="写测试",
        description="为指定代码写单元测试",
        template=(
            "请为以下代码编写完整的单元测试：\n"
            "1. 读目标代码，列出要测的函数/方法\n"
            "2. 先写测试骨架（正常路径 + 边界 + 异常）\n"
            "3. 实现测试用例，mock 外部依赖\n"
            "4. 运行测试，确保全部通过\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="docs",
        label="写文档",
        description="为代码/项目生成文档",
        template=(
            "请为以下目标生成文档：\n"
            "1. 读相关代码，理解模块职责\n"
            "2. 生成 README / API 文档 / 注释（根据上下文判断）\n"
            "3. 代码示例要可运行\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="fix",
        label="修 bug",
        description="定位并修复 bug",
        template=(
            "请定位并修复以下 bug：\n"
            "1. 读相关代码，理解预期行为\n"
            "2. 找根因（不要只改症状）\n"
            "3. 修复 + 加回归测试\n"
            "4. 验证修复有效\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="review",
        label="代码审查",
        description="审查代码质量",
        template=(
            "请审查以下代码：\n"
            "1. 正确性（逻辑错误、边界情况）\n"
            "2. 安全性（注入、泄漏、权限）\n"
            "3. 性能（N+1、内存泄漏、阻塞）\n"
            "4. 可维护性（命名、抽象、重复）\n"
            "按严重度排序，每条给修复建议。\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="run",
        label="运行验证",
        description="构建 + 运行 + 测试",
        template=(
            "请执行完整的构建/运行/测试流程：\n"
            "1. 检测项目类型（Python/Node/Go/Rust 等）\n"
            "2. 安装依赖（如需要）\n"
            "3. 构建/编译（如需要）\n"
            "4. 运行测试，报告结果\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="deps",
        label="依赖分析",
        description="分析依赖关系",
        template=(
            "请分析项目依赖关系：\n"
            "1. 读依赖声明（requirements.txt / package.json / go.mod 等）\n"
            "2. 列出直接依赖与版本\n"
            "3. 标记过时/有安全漏洞的依赖\n"
            "4. 建议升级路径\n"
            "{user_input}"
        ),
    ),
    SlashCommand(
        name="export",
        label="导出会话",
        description="把当前会话导出为文件",
        template=(
            "请调用 export_session 工具把当前会话导出（format=markdown）。\n"
            "如果 {user_input} 里指定了会话 id，请传入对应的 sid；否则提示用户先选择会话。\n"
            "导出完成后，把文件路径发给用户。\n"
            "{user_input}"
        ),
    ),
]


def _split_slash(raw: str) -> tuple[str, str] | None:
    """把 "/cmd rest..." 拆成 (小写命令名, 其余输入)；非斜杠输入返回 None。

    匹配与展开共用这一份解析，避免两处各写一遍 split 而慢慢走偏。
    """
    text = raw.strip()
    if not text.startswith("/"):
        return None
    parts = text[1:].split(" ", 1)
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


def match_slash(raw: str) -> SlashCommand | None:
    """匹配 "/cmd rest..." 格式，返回预设指令；未匹配返回 None。"""
    split = _split_slash(raw)
    if split is None:
        return None
    for c in SLASH_COMMANDS:
        if c.name == split[0]:
            return c
    return None


def expand_slash(raw: str, workdir: str) -> str:
    """把 "/cmd rest..." 展开为完整 prompt；未匹配时原样返回。"""
    cmd = match_slash(raw)
    if cmd is None:
        return raw
    user_input = _split_slash(raw)[1]  # 已确认非 None
    return cmd.template.format(user_input=user_input, workdir=workdir)


def list_commands() -> list[dict]:
    """返回所有斜杠命令的元信息（供前端提示用）。"""
    return [
        {
            "name": c.name,
            "label": c.label,
            "description": c.description,
        }
        for c in SLASH_COMMANDS
    ]
