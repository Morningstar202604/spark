"""Screenshot + click-through harness for the Spark web UI.

Unlike ``capture_screenshots.py`` (README marketing shots against a live
model), this drives a mock server and exercises every surface so a broken
button shows up as a missing state rather than a pretty picture.

Usage:
    python scripts/ux_audit.py --base http://127.0.0.1:7790 --out ../.work/ux
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CHROME = os.environ.get(
    "SPARK_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"
)
PORT = 9401


class CDP:
    def __init__(self, url: str) -> None:
        self.url = url
        self._id = 0

    async def connect(self) -> None:
        import websockets

        self.conn = await websockets.connect(self.url, max_size=128 * 1024 * 1024)

    async def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        await self.conn.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            data = json.loads(await asyncio.wait_for(self.conn.recv(), timeout=90))
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})

    async def eval(self, expr: str):
        r = await self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    async def wait(self, expr: str, timeout: float = 20, label: str = "") -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if await self.eval(expr):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        print(f"  ! wait timeout: {label or expr}", file=sys.stderr)
        return False

    async def size(self, w: int, h: int, mobile: bool = False) -> None:
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            width=w,
            height=h,
            deviceScaleFactor=2,
            mobile=mobile,
        )

    async def shot(self, out: Path, name: str) -> None:
        data = await self.send("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        (out / name).write_bytes(base64.b64decode(data["data"]))
        print(f"  {name}")

    async def click_text(self, text: str) -> bool:
        return bool(
            await self.eval(
                "(() => { const nodes=[...document.querySelectorAll('button,a,[role=button]')];"
                f" const el=nodes.find(n=>n.innerText && n.innerText.trim().includes({json.dumps(text)}));"
                " if(!el) return false; el.click(); return true; })()"
            )
        )

    async def click(self, selector: str) -> bool:
        return bool(
            await self.eval(
                "(() => { const e=document.querySelector(%s); if(!e) return false; e.click(); return true; })()"
                % json.dumps(selector)
            )
        )

    async def type(self, selector: str, text: str) -> bool:
        return bool(
            await self.eval(
                "(() => { const el=document.querySelector(%s); if(!el) return false;"
                " const proto = el.tagName==='TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;"
                " Object.getOwnPropertyDescriptor(proto,'value').set.call(el, %s);"
                " el.dispatchEvent(new Event('input', {bubbles:true})); el.focus(); return true; })()"
                % (json.dumps(selector), json.dumps(text))
            )
        )

    async def enter(self, selector: str) -> None:
        await self.eval(
            "(() => { const el=document.querySelector(%s);"
            " el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); })()"
            % json.dumps(selector)
        )

    async def close(self) -> None:
        await self.conn.close()


def launch() -> subprocess.Popen:
    profile = Path(tempfile.mkdtemp(prefix="spark-ux-"))
    return subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={profile}",
            "--window-size=1440,900",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:7790")
    ap.add_argument("--out", default="../.work/ux")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    chrome = launch()
    problems: list[str] = []
    try:
        page = None
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as r:
                    for item in json.load(r):
                        if item.get("type") == "page":
                            page = item
                            break
                if page:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        if page is None:
            print("chrome devtools never came up", file=sys.stderr)
            return 1
        cdp = CDP(page["webSocketDebuggerUrl"])
        await cdp.connect()
        await cdp.send("Page.enable")
        await cdp.send("Runtime.enable")
        await cdp.size(1440, 900)
        await cdp.send("Page.navigate", url=args.base + "/")
        await cdp.wait("!!document.querySelector('textarea')", 30, "composer")

        # 1. empty state
        await asyncio.sleep(1.2)
        await cdp.shot(out, "01-empty.png")
        if not await cdp.eval("document.body.innerText.includes('Spark')"):
            problems.append("empty state missing brand text")

        # 2. suggestion card fills the composer
        if await cdp.click_text("解读这个项目"):
            await asyncio.sleep(0.4)
            if not await cdp.eval("document.querySelector('textarea').value.length > 0"):
                problems.append("suggestion card did not fill the composer")
            await cdp.shot(out, "02-suggestion.png")

        # 3. send a turn
        await cdp.type("textarea", "list files")
        await cdp.enter("textarea")
        await cdp.wait("!document.body.innerText.includes('工作中')", 30, "turn done")
        await asyncio.sleep(1.0)
        await cdp.shot(out, "03-chat.png")

        # 4. message hover actions (delete/regenerate/edit must exist)
        if not await cdp.eval("!!document.querySelector('[title*=\"重新生成\"], [title*=\"Regenerate\"]')"):
            problems.append("assistant regenerate action missing")
        if not await cdp.eval("!!document.querySelector('[title*=\"删除这条消息\"], [title*=\"Delete this message\"]')"):
            problems.append("message delete action missing")

        # 5. web search toggle
        if not await cdp.click_text("联网搜索"):
            problems.append("web search toggle missing")
        else:
            await asyncio.sleep(0.5)
            await cdp.shot(out, "04-websearch-off.png")
            if not await cdp.eval(
                "!!document.querySelector('[aria-pressed=\"false\"]')"
            ):
                problems.append("web search toggle did not flip aria-pressed")

        # 6. fork
        if await cdp.eval("!!document.querySelector('[title*=\"分叉\"], [title*=\"Fork\"]')"):
            await cdp.eval("document.querySelector('[title*=\"分叉\"], [title*=\"Fork\"]').click()")
            await asyncio.sleep(1.5)
            await cdp.shot(out, "05-forked.png")
        else:
            problems.append("fork button missing")

        # 7. rename session
        if await cdp.eval("!!document.querySelector('[title*=\"重命名会话\"]')"):
            await cdp.eval("document.querySelector('[title*=\"重命名会话\"]').click()")
            await asyncio.sleep(0.4)
            if not await cdp.eval("!!document.querySelector('aside input[aria-label*=\"重命名\"]')"):
                problems.append("rename input did not open")
            else:
                await cdp.type("aside input[aria-label*=\"重命名\"]", "审计测试会话")
                await cdp.eval(
                    "document.querySelector('aside input[aria-label*=\"重命名\"]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))"
                )
                await asyncio.sleep(1.2)
                if not await cdp.eval("document.body.innerText.includes('审计测试会话')"):
                    problems.append("rename did not persist to the sidebar")
        else:
            problems.append("rename affordance missing")

        # 8. settings tabs + health card
        await cdp.click_text("设置")
        await asyncio.sleep(1.2)
        await cdp.shot(out, "06-settings-model.png")
        for tab, name in (("Agent", "07-settings-agent.png"), ("权限", "08-settings-security.png"), ("展示", "09-settings-display.png"), ("记忆", "10-settings-memory.png"), ("MCP", "11-settings-mcp.png"), ("项目", "12-settings-project.png")):
            if not await cdp.click_text(tab):
                problems.append(f"settings tab missing: {tab}")
                continue
            await asyncio.sleep(0.8)
            await cdp.shot(out, name)
        if not await cdp.eval("document.body.innerText.includes('环境体检')"):
            problems.append("health section missing on project tab")
        # close
        await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        await asyncio.sleep(0.6)

        # 9. command palette
        await cdp.eval("window.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}))")
        await asyncio.sleep(0.7)
        await cdp.shot(out, "13-palette.png")
        await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        await asyncio.sleep(0.5)

        # 10. execution graph should now show real nodes (root + list_dir)
        if await cdp.eval("!!document.querySelector('[title*=\"执行图\"]')"):
            await cdp.eval("document.querySelector('[title*=\"执行图\"]').click()")
            await asyncio.sleep(1.2)
            await cdp.shot(out, "14-graph.png")
            graph_text = await cdp.eval("document.body.innerText")
            if "list_dir" not in graph_text:
                problems.append("execution graph did not render the real tool call")
            await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
            await asyncio.sleep(0.4)
        else:
            problems.append("graph button missing")

        # 11. files / browser / terminal / checkpoints / replay
        for sel, name in (
            ("[title*='文件']", "15-files.png"),
            ("[title*='网页阅读']", "16-browser.png"),
            ("[title*='检查点']", "17-checkpoints.png"),
        ):
            if await cdp.eval(f"!!document.querySelector({json.dumps(sel)})"):
                await cdp.eval(f"document.querySelector({json.dumps(sel)}).click()")
                await asyncio.sleep(1.0)
                await cdp.shot(out, name)
                await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
                await asyncio.sleep(0.4)

        # 12. dark theme
        await cdp.eval("localStorage.setItem('spark-theme','dark'); location.reload()")
        await asyncio.sleep(2.5)
        await cdp.wait("!!document.querySelector('textarea')", 20, "reload")
        await cdp.shot(out, "18-dark.png")

        # 13. mobile
        await cdp.size(390, 844, mobile=True)
        await asyncio.sleep(1.0)
        await cdp.shot(out, "19-mobile.png")
        if await cdp.eval("!!document.querySelector('[aria-label*=\"菜单\"], header button')"):
            pass

        await cdp.close()
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=8)
        except subprocess.TimeoutExpired:
            chrome.kill()

    print("\nProblems:", problems or "none")
    (out / "problems.json").write_text(json.dumps(problems, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if not problems else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
