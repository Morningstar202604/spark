from __future__ import annotations

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from spark.config import ModelProfile, SparkConfig, default_home, mask_secret, require_api_key, save_config
from spark.core.loop import AgentLoop
from spark.errors import ConfigError, SparkError
from spark.memory.service import MemoryService
from spark.memory.store import MemoryStore
from spark.models import ApprovalDecision
from spark.providers.factory import create_provider
from spark.providers.probe import probe_provider
from spark.sandbox import WorkdirSandbox
from spark.store import SessionStore
from spark.tools.mcp_bridge import McpBridge
from spark.tools.registry import ToolContext, ToolRegistry

INDEX_HTML = Path(__file__).with_name("index.html")


def _extract_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    return str(msg.get("content") or "").strip()


def _generate_session_meta_async(state: "SparkWebState", user_text: str, assistant_text: str) -> None:
    """After a turn completes, ask the model for a short title + keywords and store them."""

    def run() -> None:
        import re

        import httpx

        base_url = state.cfg.provider.base_url.rstrip("/")
        api_key = state.cfg.provider.api_key or ""
        model = state.cfg.provider.model
        if not model or (state.cfg.provider.name == "openai_compat" and not api_key):
            return
        try:
            convo = f"[user]\n{user_text[:2000]}\n\n[assistant]\n{assistant_text[:2000]}"
            prompt = (
                "为这段对话生成元信息。严格输出一行 JSON，格式："
                '{"title": "不超过12字的标题", "keywords": ["关键词1", "关键词2", "关键词3"]}。'
                "标题概括用户核心意图，关键词提炼 2-4 个主题词。只输出 JSON，不要其他内容。\n\n"
                f"对话：\n{convo}"
            )
            timeout = httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=10.0)
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "max_tokens": 120,
                    "temperature": 0.2,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            text = _extract_text(resp.json())
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return
            meta = json.loads(match.group(0))
            title = str(meta.get("title") or "").strip().strip("\"'")[:40]
            raw_kws = meta.get("keywords")
            keywords = [str(k).strip()[:20] for k in raw_kws if str(k).strip()][:4] if isinstance(raw_kws, list) else []
            if title:
                state.store.set_title(state.session_id, title)
            if keywords:
                state.store.set_keywords(state.session_id, " ".join(keywords))
        except Exception:
            pass

    threading.Thread(target=run, daemon=True, name="session-meta").start()


class WebApprover:
    """Bridges approval prompts from the agent loop thread to HTTP handlers."""

    def __init__(self) -> None:
        self.pending = False
        self._event = threading.Event()
        self._action = "deny"

    async def __call__(self, request) -> ApprovalDecision:
        self.pending = True
        self._action = "deny"
        self._event = threading.Event()
        waited = 0.0
        try:
            while not self._event.is_set() and waited < 600.0:
                await asyncio.sleep(0.2)
                waited += 0.2
        finally:
            self.pending = False
        return ApprovalDecision(action=self._action)

    def respond(self, action: str) -> None:
        self._action = action
        self._event.set()


class SparkWebState:
    def __init__(self, workdir: Path, cfg: SparkConfig, store: SessionStore) -> None:
        self.workdir = workdir
        self.cfg = cfg
        self.store = store
        self.sandbox = WorkdirSandbox(workdir, cfg)
        self.session_id = ""
        self.loop: AgentLoop | None = None
        self.turn_lock = threading.Lock()
        self.busy = False
        self.approver = WebApprover() if cfg.agent.approval == "suggest" else None
        self.mcp_bridge: McpBridge | None = None
        self.memory_store = MemoryStore(default_home() / "memory.db", cfg.memory)
        self.memory = MemoryService(self.memory_store, cfg)
        self.env_info = self._detect_env()
        self.boot_mcp()
        self.rebuild_loop(reuse_latest=True)

    @staticmethod
    def _detect_env() -> dict:
        from spark.tools.shell import detect_env

        try:
            return detect_env(Path.cwd())
        except Exception:
            return {}

    def boot_mcp(self) -> list[str]:
        if self.mcp_bridge is not None:
            asyncio.run(self.mcp_bridge.close())
            self.mcp_bridge = None
        if not self.cfg.mcp_servers:
            return []
        bridge = McpBridge()
        dummy = ToolRegistry(ToolContext(sandbox=self.sandbox, config=self.cfg))
        asyncio.run(bridge.start(self.cfg.mcp_servers, dummy))
        self.mcp_bridge = bridge
        return list(bridge.errors)

    def rebuild_loop(self, session_id: str | None = None, reuse_latest: bool = False) -> None:
        tool_ctx = ToolContext(sandbox=self.sandbox, config=self.cfg)
        registry = ToolRegistry(tool_ctx)
        if self.mcp_bridge is not None:
            self.mcp_bridge.register_into(registry)
            tool_ctx.mcp_call = self.mcp_bridge.call
        provider = create_provider(self.cfg)
        if session_id is None and reuse_latest:
            latest = next(
                (row for row in self.store.list_sessions() if row["workdir"] == str(self.workdir)),
                None,
            )
            session_id = latest["id"] if latest else None
        if session_id is None:
            self.session_id = self.store.create_session(self.workdir, self.cfg.provider.model, title="web preview")
        else:
            self.session_id = session_id
        approver = self.approver if self.cfg.agent.approval == "suggest" else None
        self.loop = AgentLoop(
            workdir=self.workdir,
            cfg=self.cfg,
            provider=provider,
            registry=registry,
            store=self.store,
            session_id=self.session_id,
            approver=approver,
            memory=self.memory,
        )

    def status(self) -> dict:
        key = self.cfg.provider.api_key or ""
        usage = {"used": 0, "limit": self.cfg.context.max_context_tokens, "percent": 0.0}
        plan: list[dict] = []
        if self.loop is not None:
            try:
                usage = self.loop._usage()
            except Exception:
                pass
            plan = list(self.loop.registry.plan)
        return {
            "session_id": self.session_id,
            "workdir": str(self.workdir),
            "sandbox_mode": self.cfg.agent.sandbox_mode,
            "display": {
                "show_thinking": self.cfg.agent.show_thinking,
                "show_tools": self.cfg.agent.show_tools,
                "show_plan": self.cfg.agent.show_plan,
                "show_context": self.cfg.agent.show_context,
                "show_keywords": self.cfg.agent.show_keywords,
                "show_notices": self.cfg.agent.show_notices,
            },
            "env": self.env_info,
            "model": self.cfg.provider.model,
            "provider": self.cfg.provider.name,
            "approval": self.cfg.agent.approval,
            "base_url": self.cfg.provider.base_url,
            "api_key_masked": mask_secret(key),
            "has_api_key": bool(key),
            "mcp_servers": [s.name for s in self.cfg.mcp_servers],
            "mcp_errors": list(self.mcp_bridge.errors) if self.mcp_bridge else [],
            "context": usage,
            "plan": plan,
        }

    def ensure_default_profile(self) -> None:
        if self.cfg.model_profiles:
            return
        if not self.cfg.provider.model:
            return
        self.cfg.model_profiles = [
            ModelProfile(
                id=uuid.uuid4().hex[:8],
                name=self.cfg.provider.model,
                provider=self.cfg.provider.name,
                base_url=self.cfg.provider.base_url,
                model=self.cfg.provider.model,
                api_key=self.cfg.provider.api_key or "",
            )
        ]
        self.cfg.active_profile_id = self.cfg.model_profiles[0].id
        save_config(self.cfg)

    def full_config(self) -> dict:
        return {
            "workdir": str(self.workdir),
            "provider": {
                "name": self.cfg.provider.name,
                "base_url": self.cfg.provider.base_url,
                "model": self.cfg.provider.model,
                "api_key_env": self.cfg.provider.api_key_env,
                "api_key_masked": mask_secret(self.cfg.provider.api_key or ""),
                "has_api_key": bool(self.cfg.provider.api_key),
            },
            "agent": {
                "approval": self.cfg.agent.approval,
                "workdir_only": self.cfg.agent.workdir_only,
                "sandbox_mode": self.cfg.agent.sandbox_mode,
                "protected_paths": list(self.cfg.agent.protected_paths),
                "shell_timeout_sec": self.cfg.agent.shell_timeout_sec,
                "max_tool_rounds": self.cfg.agent.max_tool_rounds,
                "max_output_chars": self.cfg.agent.max_output_chars,
                "show_thinking": self.cfg.agent.show_thinking,
                "show_tools": self.cfg.agent.show_tools,
                "show_plan": self.cfg.agent.show_plan,
                "show_context": self.cfg.agent.show_context,
                "show_keywords": self.cfg.agent.show_keywords,
                "show_notices": self.cfg.agent.show_notices,
            },
            "context": {
                "agents_md": self.cfg.context.agents_md,
                "max_fragment_chars": self.cfg.context.max_fragment_chars,
                "history_budget_chars": self.cfg.context.history_budget_chars,
                "max_context_tokens": self.cfg.context.max_context_tokens,
                "compact_threshold": self.cfg.context.compact_threshold,
                "keep_recent_messages": self.cfg.context.keep_recent_messages,
            },
            "mcp_servers": [
                {
                    "name": s.name,
                    "command": s.command,
                    "args": s.args,
                    "readonly_tools": s.readonly_tools,
                }
                for s in self.cfg.mcp_servers
            ],
            "mcp_errors": list(self.mcp_bridge.errors) if self.mcp_bridge else [],
        }


def _event_payload(event) -> dict:
    payload: dict = {"type": event.type}
    if event.text:
        payload["text"] = event.text
    if event.tool_call:
        payload["tool"] = {
            "id": event.tool_call.id,
            "name": event.tool_call.name,
            "arguments": event.tool_call.arguments,
        }
    if event.result:
        payload["ok"] = event.result.ok
        payload["result"] = event.result.payload
    if event.approval:
        payload["approval"] = {
            "summary": event.approval.summary,
            "diff": event.approval.diff,
        }
    if event.data:
        payload["data"] = event.data
    return payload


def serve_web(*, workdir: Path, cfg: SparkConfig, store: SessionStore, host: str = "0.0.0.0", port: int = 8000) -> None:
    state = SparkWebState(workdir, cfg, store)
    html = INDEX_HTML.read_text(encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            sys_stderr = __import__("sys").stderr
            sys_stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _json(self, code: int, body: dict | list) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _html(self) -> None:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/index.html"}:
                self._html()
                return
            if path == "/api/status":
                self._json(200, state.status())
                return
            if path == "/api/config":
                state.ensure_default_profile()
                self._json(200, state.full_config())
                return
            if path == "/api/models":
                state.ensure_default_profile()
                profiles = [
                    {
                        "id": p.id,
                        "name": p.name,
                        "provider": p.provider,
                        "base_url": p.base_url,
                        "model": p.model,
                        "api_key_masked": mask_secret(p.api_key) if p.api_key else "",
                        "has_api_key": bool(p.api_key),
                        "active": p.id == state.cfg.active_profile_id,
                    }
                    for p in state.cfg.model_profiles
                ]
                self._json(200, {"profiles": profiles})
                return
            if path == "/api/agents_md":
                path_md = state.workdir / state.cfg.context.agents_md
                content = path_md.read_text(encoding="utf-8") if path_md.exists() else ""
                self._json(200, {
                    "filename": state.cfg.context.agents_md,
                    "exists": path_md.exists(),
                    "content": content,
                    "chars": len(content),
                    "max_fragment_chars": state.cfg.context.max_fragment_chars,
                })
                return
            if path == "/api/memory":
                query = urlparse(self.path).query
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                q = params.get("q", "")
                items = state.memory.list_memories()
                search_hits: list[dict] = []
                if q:
                    try:
                        for row, score in state.memory.search(q, top_k=8):
                            entry = row.to_dict()
                            entry["score"] = score
                            search_hits.append(entry)
                    except Exception as exc:
                        search_hits = [{"error": str(exc)}]
                self._json(200, {"items": items, "search": search_hits, "stats": state.memory.stats()})
                return
            if path == "/api/sessions":
                rows = state.store.list_sessions()
                sessions = [
                    {k: row.get(k) for k in ("id", "title", "keywords", "workdir", "model", "created_at", "updated_at")}
                    for row in rows
                    if row["workdir"] == str(state.workdir)
                ][:50]
                self._json(200, {"sessions": sessions, "current": state.session_id})
                return
            if path == "/api/checkpoints":
                cps = state.store.list_checkpoints(state.session_id)
                self._json(200, {"checkpoints": cps})
                return
            if path == "/api/history":
                messages = []
                if state.loop:
                    for msg in state.loop.history:
                        entry: dict = {"role": msg.role if msg.role != "summary" else "assistant", "content": msg.content or ""}
                        if msg.role == "tool":
                            entry = {"role": "tool", "name": msg.name, "content": msg.content or ""}
                        elif msg.role == "summary":
                            entry = {"role": "assistant", "content": "[conversation summary]\n" + (msg.content or "")}
                        elif msg.role in {"user", "assistant"}:
                            if msg.images:
                                entry["images"] = [f"data:{i.media_type};base64,{i.data}" for i in msg.images]
                            if not msg.content:
                                continue
                        else:
                            continue
                        messages.append(entry)
                self._json(200, {"session_id": state.session_id, "messages": messages})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                body = self._read_json()
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            if path == "/api/models/save":
                name = str(body.get("name") or "").strip()
                base_url = str(body.get("base_url") or "").strip()
                model = str(body.get("model") or "").strip()
                provider_name = str(body.get("provider") or "openai_compat").strip()
                api_key = str(body.get("api_key") or "").strip()
                if provider_name not in {"openai_compat", "ollama", "mock"}:
                    self._json(400, {"error": "provider must be openai_compat, ollama or mock"})
                    return
                if not base_url or not model:
                    self._json(400, {"error": "base_url and model are required"})
                    return
                pid = str(body.get("id") or "").strip()
                existing = next((p for p in state.cfg.model_profiles if p.id == pid), None)
                if existing:
                    existing.name = name or existing.name or existing.model
                    existing.provider = provider_name  # type: ignore[assignment]
                    existing.base_url = base_url
                    existing.model = model
                    if api_key:
                        existing.api_key = api_key
                else:
                    if not api_key:
                        api_key = state.cfg.provider.api_key or ""
                    existing = ModelProfile(
                        id=uuid.uuid4().hex[:8],
                        name=name or model,
                        provider=provider_name,  # type: ignore[arg-type]
                        base_url=base_url,
                        model=model,
                        api_key=api_key,
                    )
                    state.cfg.model_profiles.append(existing)
                save_config(state.cfg)
                self._json(200, {"ok": True, "id": existing.id})
                return
            if path == "/api/models/delete":
                pid = str(body.get("id") or "").strip()
                before = len(state.cfg.model_profiles)
                state.cfg.model_profiles = [p for p in state.cfg.model_profiles if p.id != pid]
                if len(state.cfg.model_profiles) == before:
                    self._json(404, {"error": "unknown profile"})
                    return
                if state.cfg.active_profile_id == pid:
                    state.cfg.active_profile_id = ""
                save_config(state.cfg)
                self._json(200, {"ok": True})
                return
            if path == "/api/models/activate":
                if state.busy:
                    self._json(409, {"error": "agent is busy"})
                    return
                pid = str(body.get("id") or "").strip()
                profile = next((p for p in state.cfg.model_profiles if p.id == pid), None)
                if not profile:
                    self._json(404, {"error": "unknown profile"})
                    return
                state.cfg.provider.name = profile.provider
                state.cfg.provider.base_url = profile.base_url
                state.cfg.provider.model = profile.model
                if profile.api_key:
                    state.cfg.provider.api_key = profile.api_key
                state.cfg.active_profile_id = profile.id
                save_config(state.cfg)
                try:
                    with state.turn_lock:
                        state.rebuild_loop(session_id=state.session_id)
                except SparkError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, {"ok": True, "status": state.status()})
                return
            if path == "/api/agents_md":
                if state.busy:
                    self._json(409, {"error": "agent is busy"})
                    return
                content = str(body.get("content") or "")
                filename = state.cfg.context.agents_md
                if "/" in filename or ".." in filename:
                    self._json(400, {"error": "invalid agents_md filename"})
                    return
                target = state.workdir / filename
                if content.strip():
                    target.write_text(content, encoding="utf-8")
                elif target.exists():
                    target.write_text("", encoding="utf-8")
                self._json(200, {"ok": True, "chars": len(content), "max_fragment_chars": state.cfg.context.max_fragment_chars})
                return
            if path == "/api/memory":
                action = str(body.get("action") or "").strip()
                if action == "add":
                    content = str(body.get("content") or "").strip()
                    if not content:
                        self._json(400, {"error": "content required"})
                        return
                    mtype = str(body.get("type") or "general")
                    if mtype not in {"preference", "project", "lesson", "general"}:
                        mtype = "general"
                    try:
                        importance = float(body.get("importance", 8))
                    except (TypeError, ValueError):
                        importance = 8.0
                    mid = state.memory.add_manual(content, mtype, importance)
                    self._json(200, {"ok": True, "id": mid, "stats": state.memory.stats()})
                    return
                if action == "delete":
                    mid = body.get("id")
                    if not isinstance(mid, int):
                        self._json(400, {"error": "id required"})
                        return
                    state.memory.delete(mid)
                    self._json(200, {"ok": True, "stats": state.memory.stats()})
                    return
                if action == "optimize":
                    if state.busy:
                        self._json(409, {"error": "agent is busy"})
                        return
                    report = state.memory.optimize()
                    self._json(200, {"ok": True, "report": report})
                    return
                self._json(400, {"error": "action must be add, delete or optimize"})
                return
            if path == "/api/cancel":
                if state.loop:
                    state.loop.cancel()
                self._json(200, {"ok": True})
                return
            if path == "/api/checkpoints/rollback":
                target = body.get("checkpoint_id")
                if not isinstance(target, int):
                    self._json(400, {"error": "checkpoint_id (int) required"})
                    return
                if state.busy:
                    self._json(409, {"error": "agent is busy"})
                    return
                if state.loop is None:
                    self._json(500, {"error": "agent not ready"})
                    return
                try:
                    with state.turn_lock:
                        report = state.loop.rollback_to_checkpoint(target)
                except ValueError as exc:
                    self._json(404, {"error": str(exc)})
                    return
                except Exception as exc:
                    self._json(500, {"error": f"rollback failed: {exc}"})
                    return
                messages = []
                for msg in state.loop.history or []:
                    if msg.role in {"user", "assistant"} and msg.content:
                        messages.append({"role": msg.role, "content": msg.content})
                    elif msg.role == "tool":
                        messages.append({"role": "tool", "name": msg.name, "content": msg.content})
                self._json(200, {"ok": True, "report": report, "status": state.status(), "messages": messages})
                return
            if path == "/api/approval":
                if state.approver is None:
                    self._json(400, {"error": "approval mode is not suggest"})
                    return
                action = str(body.get("decision") or "").strip()
                if action not in {"allow", "allow_always", "deny"}:
                    self._json(400, {"error": "decision must be allow, allow_always or deny"})
                    return
                state.approver.respond(action)
                self._json(200, {"ok": True})
                return
            if path == "/api/sessions/new":
                if state.busy:
                    self._json(409, {"error": "agent is busy"})
                    return
                with state.turn_lock:
                    state.rebuild_loop()
                self._json(200, {"ok": True, "status": state.status()})
                return
            if path == "/api/sessions/delete":
                target = str(body.get("session_id") or "").strip()
                if not target:
                    self._json(400, {"error": "session_id required"})
                    return
                if state.busy:
                    self._json(409, {"error": "agent is busy"})
                    return
                deleting_active = target == state.session_id
                state.store.delete_session(target)
                if deleting_active:
                    with state.turn_lock:
                        remaining = [r for r in state.store.list_sessions() if r["workdir"] == str(state.workdir)]
                        if remaining:
                            state.rebuild_loop(session_id=remaining[0]["id"])
                        else:
                            state.rebuild_loop()
                self._json(200, {"ok": True, "status": state.status()})
                return
            if path == "/api/sessions/switch":
                target = str(body.get("session_id") or "").strip()
                row = state.store.get_session(target)
                if not row:
                    self._json(404, {"error": "unknown session"})
                    return
                if state.busy:
                    self._json(409, {"error": "agent is busy"})
                    return
                with state.turn_lock:
                    state.rebuild_loop(session_id=target)
                messages = []
                for msg in state.loop.history or []:
                    if msg.role in {"user", "assistant"} and msg.content:
                        messages.append({"role": msg.role, "content": msg.content})
                    elif msg.role == "tool":
                        messages.append({"role": "tool", "name": msg.name, "content": msg.content})
                self._json(200, {"ok": True, "status": state.status(), "messages": messages})
                return
            if path == "/api/chat/stream":
                prompt = str(body.get("prompt") or "").strip()
                if not prompt and not body.get("images"):
                    self._json(400, {"error": "prompt required"})
                    return
                if state.loop is None:
                    self._json(500, {"error": "agent not ready"})
                    return
                images = []
                import base64 as _b64mod

                for item in body.get("images") or []:
                    if not isinstance(item, str) or not item.startswith("data:image/"):
                        continue
                    try:
                        head, _, b64part = item.partition(",")
                        media_type = head.removeprefix("data:").split(";")[0] or "image/png"
                        _b64mod.b64decode(b64part, validate=True)
                        images.append({"media_type": media_type, "data": b64part})
                    except Exception:
                        continue
                from spark.models import ImageRef

                image_refs = [ImageRef.model_validate(i) for i in images] or None
                if not prompt and image_refs:
                    prompt = "请分析这些图片。"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()

                def send_event(event) -> None:
                    payload = _event_payload(event)
                    self.wfile.write(b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n")

                memory_collector: dict = {"text": "", "tools": []}

                def collect_event(event) -> None:
                    if event.type == "text_delta" and event.text:
                        memory_collector["text"] += event.text
                    elif event.type == "turn_end" and event.text:
                        memory_collector["text"] = event.text
                    elif event.type == "tool_end" and event.tool_call:
                        memory_collector["tools"].append(f"{event.tool_call.name}: {json.dumps(event.result.payload if event.result else {}, ensure_ascii=False)[:200]}")

                try:
                    with state.turn_lock:
                        state.busy = True
                        try:
                            for event in state.loop.iter_turn_sync(prompt, images=image_refs):
                                collect_event(event)
                                send_event(event)
                        finally:
                            state.busy = False
                    self.wfile.write(b"data: {\"type\": \"done\"}\n\n")
                    turn_text = memory_collector["text"]
                    if turn_text:
                        _generate_session_meta_async(state, prompt, turn_text)
                    if state.cfg.memory.enabled and state.cfg.memory.auto_extract and state.memory is not None and not state.cfg.provider.name == "mock":
                        def run_memory() -> None:
                            try:
                                state.memory.record_turn(
                                    user_text=prompt,
                                    assistant_text=turn_text,
                                    tool_summary=" | ".join(memory_collector["tools"]),
                                    session_id=state.session_id,
                                )
                            except Exception:
                                pass
                        threading.Thread(target=run_memory, daemon=True).start()
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    try:
                        err = json.dumps({"type": "turn_error", "text": str(exc)}, ensure_ascii=False)
                        self.wfile.write(b"data: " + err.encode("utf-8") + b"\n\n")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                return
            if path == "/api/chat":
                prompt = str(body.get("prompt") or "").strip()
                if not prompt:
                    self._json(400, {"error": "prompt required"})
                    return
                if state.loop is None:
                    self._json(500, {"error": "agent not ready"})
                    return
                try:
                    with state.turn_lock:
                        events = asyncio.run(state.loop.run(prompt))
                except Exception as exc:
                    self._json(500, {"error": str(exc)})
                    return
                self._json(200, {"events": [_event_payload(e) for e in events]})
                return
            if path == "/api/settings":
                if state.busy:
                    self._json(409, {"error": "agent is busy"})
                    return
                provider_in = body.get("provider") if isinstance(body.get("provider"), dict) else {}
                agent_in = body.get("agent") if isinstance(body.get("agent"), dict) else {}
                base_url = str(provider_in.get("base_url") or body.get("base_url") or state.cfg.provider.base_url).strip()
                model = str(provider_in.get("model") or body.get("model") or state.cfg.provider.model).strip()
                api_key = str(provider_in.get("api_key") or body.get("api_key") or "").strip()
                provider_name = str(provider_in.get("name") or state.cfg.provider.name).strip()
                if provider_name not in {"openai_compat", "ollama", "mock"}:
                    self._json(400, {"error": "provider must be openai_compat, ollama or mock"})
                    return
                if not base_url or not model:
                    self._json(400, {"error": "base_url and model are required"})
                    return
                if provider_name == "openai_compat":
                    api_key = api_key or state.cfg.provider.api_key or ""
                    if not api_key:
                        try:
                            api_key = require_api_key(state.cfg) or ""
                        except ConfigError:
                            self._json(400, {"error": "api_key is required"})
                            return
                state.cfg.provider.name = provider_name  # type: ignore[assignment]
                state.cfg.provider.base_url = base_url
                state.cfg.provider.model = model
                state.cfg.provider.api_key = api_key or None
                approval = str(agent_in.get("approval") or state.cfg.agent.approval).strip()
                if approval not in {"suggest", "auto-edit", "full-auto"}:
                    self._json(400, {"error": "approval must be suggest, auto-edit or full-auto"})
                    return
                state.cfg.agent.approval = approval  # type: ignore[assignment]
                sandbox_mode = str(agent_in.get("sandbox_mode") or state.cfg.agent.sandbox_mode).strip()
                if sandbox_mode not in {"sandbox-only", "workspace", "full-access", "unrestricted"}:
                    self._json(400, {"error": "sandbox_mode must be sandbox-only, workspace, full-access or unrestricted"})
                    return
                state.cfg.agent.sandbox_mode = sandbox_mode  # type: ignore[assignment]
                display_flags = {k: bool(agent_in[k]) for k in ("show_thinking", "show_tools", "show_plan", "show_context", "show_keywords", "show_notices") if k in agent_in}
                for key, value in display_flags.items():
                    setattr(state.cfg.agent, key, value)
                if "protected_paths" in agent_in:
                    pp = agent_in.get("protected_paths")
                    if isinstance(pp, list):
                        state.cfg.agent.protected_paths = [str(x).strip() for x in pp if str(x).strip()]
                state.sandbox = WorkdirSandbox(state.workdir, state.cfg)
                try:
                    state.cfg.agent.workdir_only = bool(agent_in.get("workdir_only", state.cfg.agent.workdir_only))
                    state.cfg.agent.shell_timeout_sec = max(1, int(agent_in.get("shell_timeout_sec", state.cfg.agent.shell_timeout_sec)))
                    state.cfg.agent.max_tool_rounds = max(1, int(agent_in.get("max_tool_rounds", state.cfg.agent.max_tool_rounds)))
                    state.cfg.agent.max_output_chars = max(200, int(agent_in.get("max_output_chars", state.cfg.agent.max_output_chars)))
                except (TypeError, ValueError):
                    self._json(400, {"error": "invalid agent numbers"})
                    return
                mcp_in = body.get("mcp_servers")
                mcp_errors: list[str] = []
                if isinstance(mcp_in, list):
                    servers = []
                    for item in mcp_in:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("name") or "").strip()
                        command = str(item.get("command") or "").strip()
                        if not name or not command:
                            self._json(400, {"error": "each MCP server needs name and command"})
                            return
                        args_raw = item.get("args")
                        ro_raw = item.get("readonly_tools")
                        args = [str(a) for a in args_raw] if isinstance(args_raw, list) else []
                        readonly = [str(a) for a in ro_raw] if isinstance(ro_raw, list) else []
                        servers.append({"name": name, "command": command, "args": args, "readonly_tools": readonly})
                    try:
                        state.cfg.mcp_servers = servers  # type: ignore[assignment]
                    except Exception as exc:
                        self._json(400, {"error": f"invalid mcp_servers: {exc}"})
                        return
                    try:
                        mcp_errors = state.boot_mcp()
                    except Exception as exc:
                        mcp_errors = [f"MCP boot failed: {exc}"]
                save_config(state.cfg)
                try:
                    with state.turn_lock:
                        state.rebuild_loop(session_id=state.session_id)
                except SparkError as exc:
                    self._json(400, {"error": str(exc)})
                    return
                self._json(200, {"ok": True, "status": state.status(), "mcp_errors": mcp_errors, "config": state.full_config()})
                return
            if path == "/api/test":
                base_url = str(body.get("base_url") or state.cfg.provider.base_url).strip()
                model = str(body.get("model") or state.cfg.provider.model).strip()
                api_key = str(body.get("api_key") or "").strip() or (state.cfg.provider.api_key or "")
                if not api_key:
                    try:
                        api_key = require_api_key(state.cfg) or ""
                    except ConfigError as exc:
                        self._json(400, {"ok": False, "error": str(exc)})
                        return
                result = asyncio.run(probe_provider(base_url=base_url, api_key=api_key, model=model, rounds=2))
                self._json(200 if result.ok else 502, result.public_dict())
                return
            self._json(404, {"error": "not found"})

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Spark web preview at http://{host}:{port} session={state.session_id}", flush=True)
    try:
        server.serve_forever()
    finally:
        store.close()
