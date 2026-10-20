"""Capture the full screenshot set for the README.

Drives Chrome over CDP against a live Spark server with a real model:
  * landing / empty state
  * real conversation showing reasoning + tool call + answer
  * settings (model, permissions, display)
  * file viewer
  * command palette
  * mobile (390px) responsive layout
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import sys
import time
import urllib.request
from pathlib import Path

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = os.environ.get("SPARK_SHOT_BASE", "http://127.0.0.1:7788")
OUT = Path(__file__).resolve().parents[1] / "assets" / "screenshots"
PROFILE = Path(tempfile.mkdtemp(prefix="spark-chrome-"))
PORT = 9222


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
        except Exception:
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
            except Exception:
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

    async def click(self, selector: str) -> bool:
        return bool(await self.eval(
            f"(() => {{ const e=document.querySelector({json.dumps(selector)}); if(!e) return false; e.click(); return true; }})()"
        ))

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

        # ---- desktop 1440 ------------------------------------------------
        await cdp.size(1440, 900)
        await cdp.send("Page.navigate", url=BASE + "/")
        await asyncio.sleep(3)
        await cdp.wait("document.querySelector('textarea') !== null", 30, "composer")
        await asyncio.sleep(1.2)
        await cdp.shot("10-empty-state.png")

        # real turn: reasoning + read_file + answer
        await cdp.type("textarea", "读取 greet.py，并用一句话说明它是做什么的")
        await asyncio.sleep(0.3)
        await cdp.enter("textarea")
        await cdp.wait(
            "!document.body.innerText.includes('正在生成') && !document.body.innerText.includes('模型正在回复')",
            200, "turn finished",
        )
        await asyncio.sleep(2)
        await cdp.shot("11-chat.png")

        # settings
        await cdp.click_text("设置")
        await asyncio.sleep(1.2)
        await cdp.shot("12-settings-model.png")

        # permissions tab
        if await cdp.click_text("权限"):
            await asyncio.sleep(0.8)
            await cdp.shot("13-settings-permissions.png")

        # display tab
        if await cdp.click_text("显示"):
            await asyncio.sleep(0.8)
            await cdp.shot("14-settings-display.png")

        # close settings
        await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        await asyncio.sleep(0.8)

        # command palette
        await cdp.eval("window.dispatchEvent(new KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}))")
        await asyncio.sleep(0.8)
        await cdp.shot("15-command-palette.png")
        await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
        await asyncio.sleep(0.5)

        # file viewer
        if await cdp.click('[title*="文件"], [aria-label*="文件"]'):
            await asyncio.sleep(1.5)
            await cdp.shot("16-file-viewer.png")
            await cdp.eval("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
            await asyncio.sleep(0.5)

        # ---- mobile 390 ---------------------------------------------------
        await cdp.size(390, 844, mobile=True)
        await asyncio.sleep(1.5)
        await cdp.shot("17-mobile-chat.png")
        await cdp.click_text("设置")
        await asyncio.sleep(1.2)
        await cdp.shot("18-mobile-settings.png")

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
