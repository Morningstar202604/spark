"""真实 uvicorn 端到端验证（手动运行，不进 pytest 默认套件）。

进程内测试传输层（TestClient / ASGITransport）会先收完整个响应体，
无法模拟"SSE 流打开期间并发回发审批"，所以真正的流内审批链路
必须起一个真实 uvicorn 服务器来验证——本脚本就是干这个的。

用法：python3 tests/e2e_manual.py
覆盖：允许路径（审批→工具→落盘）、拒绝路径、会话并发 409 保护。
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

HERE = Path(__file__).parent


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


RUNNER = '''
import json, sys
from pathlib import Path
from spark2.store import SessionStore
from spark2.web.server import AppState, create_app
cfg = json.loads(Path(sys.argv[1]).read_text())
state = AppState(cfg=cfg, store=SessionStore(root=Path(sys.argv[2])))
import uvicorn
uvicorn.run(create_app(state), host="127.0.0.1", port=int(sys.argv[3]), log_level="error")
'''


async def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="e2e-"))
    workdir = root / "proj"
    workdir.mkdir()
    port = free_port()
    cfg = {
        "provider": "mock", "base_url": "", "model": "mock", "api_key": "",
        "workdir": str(workdir), "approval_mode": "suggest",
        "max_context_tokens": 32000, "token": "e2e-token",
        "mock_script": [
            [{"type": "tool_calls", "calls": [{"id": "w1", "name": "write_file", "arguments": {"path": "a.txt", "content": "hello-e2e"}}]}],
            [{"type": "text", "text": "完成。"}],
            [{"type": "tool_calls", "calls": [{"id": "w2", "name": "write_file", "arguments": {"path": "a.txt", "content": "denied-write"}}]}],
            [{"type": "text", "text": "好的，不写。"}],
        ],
    }
    cfgf = root / "cfg.json"
    cfgf.write_text(json.dumps(cfg), encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-c", RUNNER, str(cfgf), str(root / "sessions"), str(port)],
        cwd=str(HERE), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        # 等待服务就绪
        async with httpx.AsyncClient(timeout=10) as c:
            for _ in range(50):
                try:
                    r = await c.get(base + "/")
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.2)
            else:
                raise RuntimeError("服务器未就绪")

            r = await c.post(base + "/api/sessions", headers={"X-Spark-Token": "e2e-token"},
                             json={"workdir": str(workdir)})
            sid = r.json()["id"]

            async def run_stream(action: str) -> list[dict]:
                events = []
                async with c.stream(
                    "POST", base + "/api/chat/stream",
                    headers={"X-Spark-Token": "e2e-token"},
                    json={"session_id": sid, "prompt": "写个文件"},
                ) as resp:
                    assert resp.status_code == 200, resp.status_code
                    buf = ""
                    async for line in resp.aiter_lines():
                        if line == "":
                            if buf.startswith("data:"):
                                ev = json.loads(buf[5:].strip())
                                events.append(ev)
                                if ev["type"] == "approval":
                                    # 会话挂起等审批期间，同一会话第二次请求应 409
                                    r2 = await c.post(base + "/api/chat/stream",
                                                      headers={"X-Spark-Token": "e2e-token"},
                                                      json={"session_id": sid, "prompt": "并发"})
                                    assert r2.status_code == 409, r2.status_code
                                    await c.post(base + "/api/approval",
                                                 headers={"X-Spark-Token": "e2e-token"},
                                                 json={"request_id": ev["request_id"], "action": action, "tool": ev["tool"]})
                            buf = ""
                        else:
                            buf += line + "\n"
                return events

            # 场景1：允许 → 文件写入
            evs1 = await run_stream("allow")
            t1 = [e["type"] for e in evs1]
            assert "approval" in t1 and "tool_result" in t1 and "done" in t1, t1
            tr1 = next(e for e in evs1 if e["type"] == "tool_result")
            assert tr1["approved"] is True
            assert (workdir / "a.txt").read_text() == "hello-e2e", "允许路径应写入文件"
            print("E2E 允许路径 OK：审批并发回发 → 工具执行 → 文件已写入")

            # 场景2：拒绝 → 不写入（新会话，避免 always 规则残留）
            r = await c.post(base + "/api/sessions", headers={"X-Spark-Token": "e2e-token"},
                             json={"workdir": str(workdir)})
            sid2 = r.json()["id"]
            events = []
            async with c.stream(
                "POST", base + "/api/chat/stream",
                headers={"X-Spark-Token": "e2e-token"},
                json={"session_id": sid2, "prompt": "写个文件"},
            ) as resp:
                buf = ""
                async for line in resp.aiter_lines():
                    if line == "":
                        if buf.startswith("data:"):
                            ev = json.loads(buf[5:].strip())
                            events.append(ev)
                            if ev["type"] == "approval":
                                await c.post(base + "/api/approval",
                                             headers={"X-Spark-Token": "e2e-token"},
                                             json={"request_id": ev["request_id"], "action": "deny"})
                        buf = ""
                    else:
                        buf += line + "\n"
            tr2 = next(e for e in events if e["type"] == "tool_result")
            assert tr2["approved"] is False
            assert not (workdir / "a.txt").exists() or (workdir / "a.txt").read_text() == "hello-e2e"
            print("E2E 拒绝路径 OK：工具结果 approved=False，未产生新写入")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
    print("全部 E2E 通过")
