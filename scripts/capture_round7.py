"""Capture the round-7 UI surfaces (inline shell, share, notifications, model hotkey).

Drives headless Chrome over CDP against a live Spark server (mock provider is fine):
  * 20-inline-shell.png      — `!cmd` result card
  * 21-settings-display.png  — display tab incl. desktop-notification toggle
  * 22-palette-share.png     — command palette with the share entry
  * 23-share-html.png        — the exported single-file HTML opened in the browser
  * 24-mobile-shell.png      — shell card at 390px
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = os.environ.get("SPARK_SHOT_BASE", "http://127.0.0.1:7799")
OUT = Path(__file__).resolve().parents[1] / "assets" / "screenshots"
PROFILE = Path(tempfile.mkdtemp(prefix="spark-chrome7-"))
PORT = 9223


def launch() -> subprocess.Popen:
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [
            CHROME, "--headless=new", f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE}", "--window-size=1440,900",
            "--hide-scrollbars", "--force-device-scale-factor=2",
            "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate,MediaRouter",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


async def debugger(timeout: float = 30) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2) as r:
                for page in json.load(r):
                    if page.get("type") == "page":
                        return page
        except Exception:  # aqg: top-level boundary — devtools not up yet
            pass
        await asyncio.sleep(0.4)
    raise RuntimeError("devtools never came up")


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
            data = json.loads(await asyncio.wait_for(self.conn.recv(), timeout=60))
            if data.get("id") == mid:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})

    async def eval(self, expr: str):
        r = await self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    async def wait(self, expr: str, timeout: float = 30, label: str = "") -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if await self.eval(expr):
                    return True
            except Exception:  # aqg: top-level boundary — transient CDP error
                pass
            await asyncio.sleep(0.3)
        print(f"  ! timeout: {label or expr}", file=sys.stderr)
        return False

    async def size(self, w: int, h: int, mobile: bool = False) -> None:
        await self.send("Emulation.setDeviceMetricsOverride", width=w, height=h,
                        deviceScaleFactor=2, mobile=mobile)

    async def shot(self, name: str, full: bool = False) -> None:
        data = await self.send("Page.captureScreenshot", format="png", captureBeyondViewport=full)
        path = OUT / name
        path.write_bytes(base64.b64decode(data["data"]))
        print(f"  {name} ({path.stat().st_size // 1024} KB)")

    async def click_text(self, text: str) -> bool:
        return bool(await self.eval(f"""(() => {{
            const nodes=[...document.querySelectorAll('button,a,[role=button]')];
            const el=nodes.find(n=>n.innerText && n.innerText.trim().includes({json.dumps(text)}));
            if(!el) return false; el.click(); return true;
        }})()"""))

    async def type(self, selector: str, text: str) -> bool:
        return bool(await self.eval(f"""(() => {{
            const el=document.querySelector({json.dumps(selector)});
            if(!el) return false;
            const proto = el.tagName==='TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto,'value').set.call(el, {json.dumps(text)});
            el.dispatchEvent(new Event('input', {{bubbles:true}}));
            el.focus();
            return true;
        }})()"""))

    async def enter(self, selector: str) -> None:
        await self.eval(f"""(() => {{
            const el=document.querySelector({json.dumps(selector)});
            el.dispatchEvent(new KeyboardEvent('keydown', {{key:'Enter', bubbles:true}}));
        }})()""")

    async def close(self) -> None:
        await self.conn.close()


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    chrome = launch()
    try:
        page = await debugger()
        cdp = CDP(page["webSocketDebuggerUrl"])
        await cdp.connect()
        await cdp.send("Page.enable")
        await cdp.send("Runtime.enable")

        await cdp.size(1440, 900)
        await cdp.send("Page.navigate", url=BASE + "/")
        await asyncio.sleep(3)
        await cdp.wait("document.querySelector('textarea') !== null", 30, "composer")
        # 关掉首用引导，避免遮住被测界面
        await cdp.eval("localStorage.setItem('spark.onboarding.done','1')")
        await cdp.eval("location.reload()")
        await asyncio.sleep(3)
        await cdp.wait("document.querySelector('textarea') !== null", 30, "composer2")
        await asyncio.sleep(1)

        # 1) inline shell card
        await cdp.type("textarea", "!echo Spark inline shell && ls")
        await asyncio.sleep(0.4)
        await cdp.shot("20-inline-shell-hint.png")
        await cdp.enter("textarea")
        await cdp.wait("document.body.innerText.includes('hello') || document.body.innerText.includes('Spark inline shell')", 60, "shell done")
        await asyncio.sleep(1.2)
        await cdp.shot("20-inline-shell.png")

        # 2) settings → display (notification toggle)
        await cdp.click_text("设置")
        await asyncio.sleep(1.2)
        if await cdp.click_text("展示"):
            await asyncio.sleep(0.9)
            await cdp.shot("21-settings-display.png")
        await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        await asyncio.sleep(0.6)

        # 3) command palette with share entry
        await cdp.eval("window.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}))")
        await asyncio.sleep(0.9)
        await cdp.type("input", "分享")
        await asyncio.sleep(0.8)
        await cdp.shot("22-palette-share.png")

        # 4) exported HTML opened in a fresh tab
        export = await cdp.eval("""(async () => {
            const r = await fetch('/api/sessions/export', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({format:'html'})});
            const j = await r.json();
            return j.content || '';
        })()""")
        if export:
            share_path = Path(tempfile.gettempdir()) / "spark-share-preview.html"
            share_path.write_text(export, encoding="utf-8")
            await cdp.send("Page.navigate", url=share_path.as_uri())
            await asyncio.sleep(1.5)
            await cdp.shot("23-share-html.png")

        # 5) mobile shell card
        await cdp.send("Page.navigate", url=BASE + "/")
        await asyncio.sleep(2.5)
        await cdp.size(390, 844, mobile=True)
        await asyncio.sleep(1.2)
        # 窄视口默认展开侧栏抽屉；先关掉再截对话区
        await cdp.eval("""(()=>{const b=document.querySelector('header button'); b&&b.click()})()""")
        await asyncio.sleep(0.8)
        await cdp.type("textarea", "!echo mobile-ok && pwd")
        await asyncio.sleep(0.3)
        await cdp.enter("textarea")
        await asyncio.sleep(3.0)
        await cdp.shot("24-mobile-shell.png")

        await cdp.close()
        return 0
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
