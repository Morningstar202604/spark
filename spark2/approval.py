"""审批门：决定每个工具调用是 放行 / 询问 / 拒绝。

两个正交维度分开表达：
- 能力：沙箱/路径规则（写保护路径 = 拒绝；写工作区外 = 必问）。
- 提问：审批档位 suggest（写+命令都问）/ auto-edit（工作区内写不问，命令问）/ full-auto（都不问）。
会话内"始终允许"以工具名记入 always 集合。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from spark2.tools.base import Tool, is_within, resolve_path


@dataclass
class ApprovalGate:
    mode: str = "suggest"
    always: set[str] = field(default_factory=set)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    _request_tool: str | None = None

    def _target_path(self, args: dict, workdir: Path) -> Path | None:
        raw = args.get("path") if isinstance(args, dict) else None
        if not raw:
            return None
        try:
            return resolve_path(str(raw), workdir)
        except (OSError, ValueError):
            return None

    def decide(
        self, tool: Tool, args: dict, workdir: Path, protected: list[Path]
    ) -> tuple[str, str]:
        """返回 (决策, 理由)：allow / ask / deny。

        路径边界永远优先于档位与"会话内始终允许"：
        - 受保护路径 → 永远 deny；
        - 工作区之外写入 → 永远 ask（即使 always / full-auto 也逐次确认）。
        """
        if tool.category == "write":
            p = self._target_path(args, workdir)
            if p is not None:
                for prot in protected:
                    if is_within(p, prot):
                        return "deny", f"受保护路径，禁止写入：{prot}"
                if not is_within(p, workdir):
                    return "ask", "写入工作区之外，需确认（不受始终允许/全自动影响）"
        if tool.category == "system":
            return "allow", ""
        if tool.name in self.always:
            return "allow", "会话内已放行"
        if self.mode == "full-auto":
            return "allow", "full-auto 模式"
        if tool.category == "write":
            if self.mode == "suggest":
                return "ask", "写入文件需确认"
            return "allow", "auto-edit 已放行工作区内写入"
        if tool.category == "shell":
            return "ask", "执行命令需确认"
        return "allow", ""

    def register(self, request_id: str, tool_name: str | None = None) -> asyncio.Future:
        """先注册审批（必须在发出 approval 事件之前调用），返回等待用的 Future。"""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self.pending[request_id] = fut
        self._request_tool = tool_name
        return fut

    async def await_result(
        self, request_id: str, fut: asyncio.Future, timeout: float = 600.0
    ) -> bool:
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(request_id, None)

    async def request(
        self, request_id: str, timeout: float = 600.0, tool_name: str | None = None
    ) -> bool:
        """便捷版：register + await_result（供非事件流场景使用）。"""
        fut = self.register(request_id, tool_name)
        return await self.await_result(request_id, fut, timeout)

    @staticmethod
    def _resolve(fut: asyncio.Future, value: bool) -> None:
        """跨线程/跨事件循环安全地完成 Future（审批响应可能来自别的请求上下文）。"""
        try:
            fut.get_loop().call_soon_threadsafe(fut.set_result, value)
        except (RuntimeError, AttributeError):
            fut.set_result(value)

    def respond(self, request_id: str, action: str, tool_name: str | None = None) -> bool:
        fut = self.pending.get(request_id)
        if not fut:
            return False
        if action == "always" and tool_name:
            self.always.add(tool_name)
            self._resolve(fut, True)
        elif action == "allow":
            self._resolve(fut, True)
        elif action == "deny":
            self._resolve(fut, False)
        else:
            return False
        return True
