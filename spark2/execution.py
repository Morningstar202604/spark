"""工具执行层：审批 → 检查点 → 执行 → 注入防护 → 回填（原 loop._execute_call 拆分）。

ToolExecutor 持有执行单个工具调用所需的全部依赖（由 AgentLoop 注入），
把 loop 里最大的一段（审批竞态、逐文件过滤、超时强杀、子 Agent 嵌入）独立成层：
- 审批门裁决 allow/ask/deny；ask 等待用户响应（含逐文件 apply_patch 过滤）
- 写类工具落盘前自动 git 检查点
- 工具输出按不可信数据处理（Prompt Injection 防护标记）
- spawn_subagent 由子 Agent 循环执行，事件直接嵌入当前流
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from spark2.approval import ApprovalGate
from spark2.memory import MemoryStore
from spark2.tools.base import Tool, ToolContext
from spark2.tools.git import git_commit, is_git_repo
from spark2.tools.injection import guard_tool_output


class ToolExecutor:
    """执行单个工具调用并产出事件（async generator）。"""

    def __init__(
        self,
        *,
        gate: ApprovalGate,
        registry: dict[str, Tool],
        ctx: ToolContext,
        cancel_event: asyncio.Event,
        tool_timeout: float,
        workdir: Path,
        memory: MemoryStore,
        log_path: Path | None,
        provider_cfg: dict,
        extra_protected: list[str] | None = None,
        auto_verify: bool = True,
    ) -> None:
        self.gate = gate
        self.registry = registry
        self.ctx = ctx
        self.cancel_event = cancel_event
        self.tool_timeout = tool_timeout
        self.workdir = workdir
        self.memory = memory
        self.log_path = log_path
        self.provider_cfg = provider_cfg
        self._extra_protected = list(extra_protected or [])
        self.auto_verify = auto_verify

    async def execute(self, messages: list[dict], call: dict) -> AsyncIterator[dict]:
        """执行单个工具调用并产出事件；错误/拒绝/取消都回填为 tool 消息。"""
        name = call.get("name", "")
        tool_id = call.get("id") or f"call_{uuid.uuid4().hex[:8]}"
        args = call.get("arguments") or {}
        tool = self.registry.get(name)

        # 参数 JSON 被输出上限截断：不执行（否则会按空参数写出错误文件），
        # 把原因回给模型让它用更小的内容重试。
        if call.get("invalid_arguments"):
            tail = str(call.get("arguments_tail") or "")[-120:]
            msg = (
                f"错误：{name} 的参数 JSON 不完整（模型输出被长度上限截断），本次未执行。"
                "请把内容拆小后重试：一次只写一个文件、单个文件不要过长，"
                f"或先写骨架再补充。收到的参数尾部：{tail}"
            )
            yield {"type": "error", "message": msg}
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": msg})
            return

        if tool is None:
            msg = f"错误：未知工具 {name}"
            yield {"type": "error", "message": msg}
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": msg})
            return

        # 计划工具：直接转为 plan 事件，不审批
        if name == "update_plan":
            steps = args.get("steps") if isinstance(args.get("steps"), list) else []
            yield {"type": "plan", "steps": steps}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": f"计划已更新：{len(steps)} 步",
                }
            )
            return

        # 子 Agent 工具：实际执行由子 Agent 循环完成，事件直接嵌入当前事件流
        # （子 Agent 的审批共用同一个 ApprovalGate，写操作照样弹窗确认）
        if name == "spawn_subagent":
            agent_type = str(args.get("agent_type") or "explore")
            yield {
                "type": "tool_start",
                "id": tool_id,
                "name": name,
                "args_summary": f"派 {agent_type} 子 Agent 执行子任务",
                "diff": "",
            }
            async for ev in self._run_subagent(messages, tool_id, args):
                yield ev
            return

        # explore_parallel：并行派出多个只读子 Agent（Agent Teams 最小版）
        if name == "explore_parallel":
            yield {
                "type": "tool_start",
                "id": tool_id,
                "name": name,
                "args_summary": "并行探索 " + self._explore_parallel_summary(args),
                "diff": "",
            }
            async for ev in self._run_explore_parallel(messages, tool_id, args):
                yield ev
            return

        decision, reason = self.gate.decide(
            tool, args, self.workdir, self.ctx.protected
        )
        if decision == "deny":
            out = f"已拒绝：{reason}"
            yield {
                "type": "tool_result",
                "id": tool_id,
                "name": name,
                "output": out,
                "approved": False,
                "duration_ms": 0,
            }
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": out})
            return

        if decision == "ask":
            summary, detail = (
                tool.preview(args, self.ctx)
                if tool.preview
                else (f"调用 {name}", json.dumps(args, ensure_ascii=False)[:4000])
            )
            request_id = uuid.uuid4().hex
            # 先注册、后发事件：避免消费方在事件到达时 respond 落空（审批竞态）
            fut = self.gate.register(request_id, tool_name=name)
            yield {
                "type": "approval",
                "request_id": request_id,
                "tool": name,
                "summary": summary,
                "diff": detail,
                "reason": reason,
            }
            decision_result = await self.gate.await_result(
                request_id, fut, timeout=600
            )
            approved, allowed_files = decision_result, None
            if isinstance(decision_result, tuple):
                approved, allowed_files = decision_result
            if not approved:
                # 区分「用户拒绝」与「等待超时」：超时时告诉模型是没人响应，
                # 让它知道可以稍后重试，而不是误以为用户否决了方案。
                out = (
                    "审批等待超时（600 秒未收到用户响应），本次未执行。"
                    "如仍需执行，请重新发起该操作。"
                    if decision_result is None
                    else "用户拒绝了本次操作。"
                )
                yield {
                    "type": "tool_result",
                    "id": tool_id,
                    "name": name,
                    "output": out,
                    "approved": False,
                    "duration_ms": 0,
                }
                messages.append(
                    {"role": "tool", "tool_call_id": tool_id, "content": out}
                )
                return
            # 逐文件审批：只应用用户勾选的文件（apply_patch 专用，其余工具忽略）
            if allowed_files and name == "apply_patch":
                args["files"] = list(allowed_files)

        if self.cancel_event.is_set():
            messages.append(
                {"role": "tool", "tool_call_id": tool_id, "content": "已取消"}
            )
            return

        # 写类工具（write_file / apply_patch 等）落盘前自动创建 git 检查点
        # （非仓库则静默跳过，不影响写入）
        if tool.category == "write" and is_git_repo(self.workdir):
            try:
                await git_commit(self.workdir, f"spark2 {tool.name} 前检查点")
            except Exception:  # noqa: BLE001
                pass

        args_summary = self._summarize_args(tool, args)
        _, diff = tool.preview(args, self.ctx) if tool.preview else ("", "")
        yield {
            "type": "tool_start",
            "id": tool_id,
            "name": name,
            "args_summary": args_summary,
            "diff": diff,
        }
        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                tool.handler(args, self.ctx), timeout=self.tool_timeout
            )
            duration = int((time.monotonic() - t0) * 1000)
            # Prompt Injection 防护：工具输出按不可信数据处理，命中注入特征则加警告标记
            safe, flagged = guard_tool_output(name, result)
            # 改完自动验证：apply_patch 成功后跑受影响测试并回填（可配置关闭）
            if name == "apply_patch" and self.auto_verify and not self.cancel_event.is_set():
                verify_note = await self._auto_verify()
                if verify_note:
                    safe = safe + "\n\n" + verify_note
            yield {
                "type": "tool_result",
                "id": tool_id,
                "name": name,
                "output": safe,
                "approved": True,
                "duration_ms": duration,
                "injected": flagged,
            }
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": safe})
        except TimeoutError:
            msg = f"工具 {name} 执行超过 {int(self.tool_timeout)}s，已终止"
            yield {"type": "error", "message": msg}
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": msg})
        except asyncio.CancelledError:
            messages.append(
                {"role": "tool", "tool_call_id": tool_id, "content": "已取消"}
            )
            raise
        except Exception as e:  # noqa: BLE001
            msg = f"工具 {name} 执行失败：{e}"
            yield {"type": "error", "message": msg}
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": msg})

    async def _run_subagent(self, messages: list[dict], tool_id: str, args: dict):
        """执行 spawn_subagent：构造子 Agent，把它的整个事件流嵌入当前流。

        子 Agent 与主 Agent 共享同一个 ApprovalGate / cancel_event：
        - 写类工具照样弹出审批（用户照常拒绝/允许）；
        - 用户点"停止"会级联终止子 Agent。
        子 Agent 结束后，把它的结论摘要作为 spawn_subagent 的 tool_result 返回主 Agent。
        """
        from spark2.subagent import make_subagent

        agent_type = str(args.get("agent_type") or "explore").strip().lower()
        if agent_type not in ("explore", "general"):
            agent_type = "explore"
        task = str(args.get("task") or "").strip()
        if not task:
            out = "错误：spawn_subagent 需要 task（任务描述）"
            yield {
                "type": "tool_result",
                "id": tool_id,
                "name": "spawn_subagent",
                "output": out,
                "approved": False,
                "duration_ms": 0,
            }
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": out})
            return

        sub = make_subagent(
            agent_type=agent_type,
            workdir=self.workdir,
            provider_cfg=self._subagent_cfg(),
            gate=self.gate,
            memory=self.memory,
            log_path=self.log_path,
            cancel_event=self.cancel_event,
            extra_protected=self._extra_protected,
        )
        sub_msgs: list[dict] = [
            {"role": "user", "content": f"[子任务 {agent_type}] {task}"}
        ]
        t0 = time.monotonic()
        text_parts: list[str] = []
        tool_steps: list[str] = []
        done_reason = "done"
        async for ev in sub.stream(sub_msgs):
            t = ev["type"]
            if t == "text":
                text_parts.append(ev.get("delta", ""))
            elif t == "tool_start":
                tool_steps.append(f"{ev.get('name', '')}: {ev.get('args_summary', '')}")
            elif t == "done":
                done_reason = ev.get("reason", "done")
                continue  # 子 Agent 的 done 不透传：否则消费方会误以为主对话结束而断流
            # 子 Agent 的其余事件（含 approval / tool_result / status / plan）透传给前端
            yield ev
        if self.cancel_event.is_set():
            done_reason = "cancelled"

        lines = []
        if tool_steps:
            lines.append(
                f"（子 Agent 执行 {len(tool_steps)} 步："
                + "；".join(tool_steps[:6])
                + "）"
            )
        body = "\n".join(x for x in text_parts if x).strip()
        if body:
            lines.append(body)
        else:
            lines.append("（子 Agent 未返回文本）")
        if done_reason == "cancelled":
            lines.append("（已取消）")
        out = "\n".join(lines)[:4000]
        dur = int((time.monotonic() - t0) * 1000)
        yield {
            "type": "tool_result",
            "id": tool_id,
            "name": "spawn_subagent",
            "output": out,
            "approved": True,
            "duration_ms": dur,
        }
        messages.append({"role": "tool", "tool_call_id": tool_id, "content": out})

    def _subagent_cfg(self, sub_idx: int | None = None) -> dict:
        """子 Agent 的 provider 配置：与主配置同源，但去掉主 mock_script（避免共享消耗）。

        mock 演示模式下，可通过 cfg["mock_subagent_script"] 给子 Agent 独立脚本，
        让演示里的子任务有真实工具调用（真实模型时该字段不存在，不受影响）。
        """
        cfg = dict(self.provider_cfg)
        cfg.pop("mock_script", None)
        sub_script = self.provider_cfg.get("mock_subagent_script")
        if isinstance(sub_script, list) and sub_script:
            # 嵌套模式：[[子Agent0 轮次...], [子Agent1 轮次...]] → 按 index 分配（并行子 agent 各自脚本）
            # 扁平模式：[轮次...] → 每个子 agent 共享同一脚本（向后兼容）
            first = sub_script[0]
            if first and isinstance(first, list) and first[0] and isinstance(first[0], list):
                idx = min(max(int(sub_idx or 0), 0), len(sub_script) - 1)
                batch = sub_script[idx]
                cfg["mock_script"] = [list(b) for b in batch] if batch else None
            else:
                cfg["mock_script"] = [list(b) for b in sub_script]
        return cfg

    def _summarize_args(self, tool: Tool, args: dict) -> str:
        if tool.name == "write_file":
            return f"写入 {args.get('path', '?')}"
        if tool.name == "run_shell":
            return f"执行：{args.get('command', '')}"
        if tool.name == "read_file":
            return f"读取 {args.get('path', '?')}"
        if tool.name == "search":
            return f"搜索 {args.get('query', '')}"
        if tool.name == "list_dir":
            return f"列出 {args.get('path', '.')}"
        return f"{tool.name} {json.dumps(args, ensure_ascii=False)[:80]}"

    # ---------- explore_parallel（并行只读探索，Agent Teams 最小版） ----------

    def _explore_parallel_summary(self, args: dict) -> str:
        topics = args.get("topics") or []
        names = []
        for t in topics[:4]:
            p = str(t.get("path") or "?").rstrip("/")
            names.append(p.split("/")[-1] or p)
        return "、".join(names) if names else "多个区域"

    async def _run_explore_parallel(
        self, messages: list[dict], tool_id: str, args: dict
    ):
        """并行派出 2-4 个只读 explore 子 Agent，汇总结论作为 tool_result。

        - 每个子 Agent 独立循环（asyncio.gather 并发），共享 cancel_event。
        - 只读探索不触发审批（explore registry 无写/命令工具）。
        - 中间事件不透传（并行流交织无法在对话流里展示），只给汇总结果；
          子 Agent 被取消时对应区域标注「已取消」。
        """
        from spark2.subagent import make_subagent

        topics = (args.get("topics") or [])[:4]
        valid = [
            t
            for t in topics
            if isinstance(t, dict) and str(t.get("path") or "").strip()
        ][:4]
        if len(valid) < 2:
            out = "错误：explore_parallel 需要至少 2 个探索主题（topics: [{path, task}, ...]）"
            yield {
                "type": "tool_result",
                "id": tool_id,
                "name": "explore_parallel",
                "output": out,
                "approved": False,
                "duration_ms": 0,
            }
            messages.append({"role": "tool", "tool_call_id": tool_id, "content": out})
            return

        t0 = time.monotonic()

        async def run_one(topic: dict, idx: int) -> tuple[str, str]:
            area = str(topic.get("path") or "").strip()
            task = str(topic.get("task") or "").strip() or f"摸清 {area} 的结构与职责"
            sub = make_subagent(
                agent_type="explore",
                workdir=self.workdir,
                provider_cfg=self._subagent_cfg(idx),  # 每次独立副本 + 按 index 取嵌套 mock 脚本
                gate=self.gate,
                memory=self.memory,
                log_path=None,  # 并行日志不写主日志（避免交织），结论随 tool_result 回传
                cancel_event=self.cancel_event,
                extra_protected=self._extra_protected,
            )
            sub_msgs = [{"role": "user", "content": f"[并行探索 {area}] {task}"}]
            text_parts: list[str] = []
            steps: list[str] = []
            async for ev in sub.stream(sub_msgs):
                t = ev["type"]
                if t == "text":
                    text_parts.append(ev.get("delta", ""))
                elif t == "tool_start":
                    steps.append(str(ev.get("name", "?")))
                elif t == "done":
                    break
            cancelled = self.cancel_event.is_set()
            lines = []
            if steps:
                lines.append("（读取 " + "、".join(steps[:6]) + "）")
            body = "\n".join(x for x in text_parts if x).strip()
            if body:
                lines.append(body)
            else:
                lines.append("（未返回文本）")
            if cancelled:
                lines.append("（已取消）")
            return area, "\n".join(lines).strip()[:2500]

        results = await asyncio.gather(
            *(run_one(t, i) for i, t in enumerate(valid)), return_exceptions=True
        )

        parts = []
        for idx, res in enumerate(results):
            area = valid[idx].get("path") or "?"
            if isinstance(res, Exception):
                parts.append(f"## {area}\n（探索失败：{res}）")
            else:
                parts.append(f"## {res[0]}\n{res[1]}")
        out = "\n\n".join(parts)[:4000]
        dur = int((time.monotonic() - t0) * 1000)
        yield {
            "type": "tool_result",
            "id": tool_id,
            "name": "explore_parallel",
            "output": out,
            "approved": True,
            "duration_ms": dur,
        }
        messages.append({"role": "tool", "tool_call_id": tool_id, "content": out})

    # ---------- 改完自动验证（apply_patch 后跑测试回填） ----------

    async def _auto_verify(self) -> str:
        """apply_patch 成功后自动运行项目测试（pytest -q，120s 超时）。

        检测条件：工作目录存在 pytest 配置（pyproject[tool.pytest.ini_options] /
        pytest.ini / setup.cfg / tox.ini）或 tests 目录，且能找到 pytest 可执行。
        不满足任一条件则跳过（返回空串，不打断对话流）。
        """
        wd = self.workdir
        has_cfg = False
        for m in (wd / "pytest.ini", wd / "setup.cfg", wd / "tox.ini"):
            if m.exists():
                has_cfg = True
                break
        if not has_cfg:
            pp = wd / "pyproject.toml"
            if pp.exists() and "[tool.pytest.ini_options]" in pp.read_text(
                encoding="utf-8", errors="ignore"
            ):
                has_cfg = True
        has_tests = (wd / "tests").is_dir()
        if not (has_cfg or has_tests):
            return ""
        pytest_bin = shutil.which("pytest")
        if not pytest_bin:
            return ""
        try:
            proc = await asyncio.create_subprocess_exec(
                pytest_bin,
                "-q",
                cwd=str(wd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                raw = await asyncio.wait_for(proc.communicate(), timeout=120)
            except TimeoutError:
                proc.kill()
                await proc.communicate()
                return "[自动验证] pytest 超过 120s 未完成，已终止（可手动运行查看）"
            if proc.returncode is None:
                return "[自动验证] 已取消"
            text = (raw[0] or b"").decode("utf-8", errors="ignore")
            tail = "\n".join(text.strip().splitlines()[-6:]).strip()
            if proc.returncode == 0:
                summary = tail.splitlines()[-1] if tail else "通过"
                return f"[自动验证] ✓ pytest {summary}"
            return f"[自动验证] ✗ pytest 未通过（exit={proc.returncode}）：\n{tail}"
        except Exception as e:  # noqa: BLE001 —— 验证失败不阻断对话
            return f"[自动验证] 执行异常：{e}"
