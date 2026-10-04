<!--
spark: local-first AI coding agent | approval gate | prompt-injection defense |
local semantic memory | MCP | web search | Chinese LLMs (DeepSeek/Qwen/GLM/Kimi/Doubao/Unisound) |
SSE streaming | native Web Components | zero-Electron | Windows EXE
-->

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/logo.svg">
  <img src="assets/logo.svg" width="96" alt="Spark logo — local-first AI coding agent" />
</picture>

# ⚡ Spark

**The local AI coding agent that asks before it acts.**

Runs on your machine · Sees every step · Approves every write & command · Zero hidden AI calls

[![License: MIT](https://img.shields.io/badge/license-MIT-14b8a6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-0f766e.svg)]()
[![CI](https://img.shields.io/github/actions/workflow/status/X33834/spark/ci.yml?branch=main&label=CI)](https://github.com/X33834/spark/actions/workflows/ci.yml)
[![Local-first](https://img.shields.io/badge/local--first-100%25%20offline-0f766e.svg)]()

**English** · [中文](README.zh.md)

</div>

---

Spark is a **local-first AI coding agent** rebuilt from the ground up — no Electron bloat, no cloud dependency, no hidden model calls. It protects you with an **approval gate** (nothing writes to disk or runs commands without your say-so), blocks **prompt injection**, remembers your project **locally**, and works with **Chinese model providers out of the box** (DeepSeek / Qwen / Zhipu / Kimi / Doubao / Ollama).

One file frontend. One process. One `pip install`. Everything lives under `~/.spark/`.

> Keywords: AI coding assistant · agent · CLI · approval gate · prompt injection protection ·
> local memory · MCP · Chinese LLMs (DeepSeek, Qwen, GLM, Kimi, Doubao, Unisound, Ollama) ·
> SSE streaming · Web Components · zero-Electron

> ✨ **Stand-out vs. other AI agents:** opencode, ZCode and Codex CLI all ship without built-in prompt-injection defense and cross-session semantic memory. Spark ships **both** — plus a lightweight plugin point, local code indexing and a cost dashboard.

---

## 🎬 See it in action

| | |
| --- | --- |
| ![Welcome](assets/screenshots/01-welcome.png) | ![Chat](assets/screenshots/02-chat.png) |
| ![Settings](assets/screenshots/03-settings.png) | ![MCP Market](assets/screenshots/04-mcp-market.png) |
| ![Terminal](assets/screenshots/05-terminal.png) | |

## 🎬 See it in action

[Watch the demo video](docs/media/promo.mp4)

## ✨ Why Spark?

| | Spark | Typical AI agent |
|---|---|---|
| 🛡️ **Approval gate** | Writes & commands **always ask first** (3 modes, path-boundary enforced) | Auto-edits silently |
| 🧠 **Prompt-injection defense** | Tool/file output treated as *data, never instructions* — flagged inline | Usually none |
| 🔒 **Local semantic memory** | FTS5 zero-dep + optional Doubao/BGE-M3 embeddings, per-workdir, **zero hidden calls** | Cloud memory or none |
| 🇨🇳 **Chinese models first** | DeepSeek / Qwen / Zhipu / Kimi / Doubao / Ollama presets, 2026 current models | OpenAI-centric |
| 🪶 **Lightweight** | Single-file web UI + one uvicorn process, no Electron | Heavy desktop shells |
| 🧩 **Extensible** | Plugins = `.py` files you drop in `~/.spark/plugins/` | Rigid or SDK-heavy |

## 🚀 Quick start

```bash
pip install -e ".[dev]"     # includes uvicorn[standard] — required for the terminal WebSocket
spark                       # 就这样。没有参数默认打开 Web 界面（浏览器访问打印的地址）
```

> 就是这么简单：安装后一个 `spark` 即可开始用。第一次启动若发现前端未构建，会自动
> 执行 `npm install && npm run build`（需本机装有 node/npm），完成后自动打开页面；
> 不想自动构建的话，可先在 `spark/web/` 下手动构建一次。
>
> If the **terminal panel** fails to connect (WS 404 / unsupported upgrade), uvicorn is
> missing websockets: `pip install "uvicorn[standard]"` and restart.
>
> **Old pip pitfalls:** On systems with pip < 23.2, `pip install -e ".[dev]"` may fail with
> "No matching distribution found for fastapi/tomlkit". Upgrade pip first:
> `python -m pip install --upgrade pip`.
>
> **External preview access:** `spark web` defaults to `--host 0.0.0.0` so it can be reached
> by external proxy / preview services. For local-only use pass `--host 127.0.0.1`.

## 📦 Packages (Windows EXE / macOS / Linux)

Prefer a binary over `pip`? Grab one from **GitHub Releases** — `git tag v0.9.0 && git push --tags`
triggers the build — or build it locally in one command:

| Platform | One command | Output |
| --- | --- | --- |
| Windows | `powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1` | `dist\spark\spark.exe` |
| Linux / macOS | `bash scripts/build.sh` | `dist/spark/spark` |

The `spark` binary bundles the CLI **and** the web UI (frontend assets embedded):
run `spark.exe web` (or `./spark web`) and open the printed URL.
Full guide: [docs/INSTALL.md](docs/INSTALL.md) · [docs/en/INSTALL.md](docs/en/INSTALL.md)

1. Open **Settings** → pick a provider (DeepSeek / Qwen / Zhipu / Kimi / Doubao / Ollama / **demo mode**) → paste your API key → point **workdir** at your project → **Save**.
2. Click **+ New session** and tell Spark what to do — *"fix the login endpoint bug"*.
3. Watch every step: plan → tool call → diff preview → **you approve** → result.

**No API key?** Switch the provider to *Demo mode* — the full interaction (plan cards, tool cards, approval popups) works with zero config.

**Headless:** `spark run "refactor lib.py" --workdir /path/to/project`
**Terminal UI:** `spark tui` — same kernel, keyboard-first (Ctrl+N new / Ctrl+S sessions / A allow / D deny / S always allow)
**Health check:** `spark doctor` — environment, config, MCP, memory, logs in one pass

## 🧰 What's inside (v0.9.0)

- 🛡️ **Approval gate** — suggest / auto-edit / full-auto; protected paths are always refused; writes outside workdir always confirmed; session-scoped "always allow"
- 🧠 **Prompt-injection defense** — every tool/file result is untrusted data; CN/EN injection patterns flagged with an inline banner; permissions always stay with the gate
- 📦 **Multi-file edits (`apply_patch`)** — one unified diff across many files, grouped per-file diff review (expand/collapse), all-or-nothing apply, git checkpoint rollback
- 🖥️ **Built-in persistent terminal (PTY)** — xterm.js panel, cwd = workdir, survives fold/refresh, multi-tab, take over input anytime (WebSocket, token-auth)
- 👥 **Multi-agent (`spawn_subagent`)** — `explore` read-only scout, `general` full-tool executor that *still goes through the approval gate*; events embedded in the main stream; shared cancel
- 🍴 **Session fork & parallel runs** — copy a session at any point into an independent branch; run many sessions concurrently with live "● running" badges
- 🔎 **Code index** — `index_project` / `search_symbol` / `lint_file`: Python via stdlib `ast` (symbols + line numbers), other languages line-level; cached per-workdir, mtime+size invalidation, read-only
- 🧩 **Lightweight plugins** — drop a `.py` into `~/.spark/plugins/` (`tools=` list or `register(reg)`); plugin tools respect the approval gate; failures never break the server
- 💰 **Cost dashboard** — per-session token/fee JSONL; top bar "Usage" panel with session ranking; 2026-09 verified official pricing (DeepSeek valley-price, Doubao Volcano Engine), overridable
- 🧠 **Local memory** — say *"remember X is Y"*; per-turn FTS5 retrieval (CN bigram aware), optional semantic (Doubao vector API / local BGE-M3, offline); per-workdir isolation
- 🔌 **MCP** — official SDK, stdio + Streamable HTTP (2026-07-28 spec); read-only auto-pass, writes go through the gate
- 💾 **Everything local** — sessions `~/.spark/sessions/`, memory `~/.spark/memory/`, usage `~/.spark/usage/`, index `~/.spark/codeindex/`; JSONL, human-readable, deletable

## 🏗️ Architecture

```mermaid
flowchart TB
    subgraph UI["Three frontends, one kernel"]
        WEB["Web · single-file index.html (SSE, mobile-first)"]
        TUI["Textual TUI"]
        CLI["Headless CLI"]
    end
    UI --> API["FastAPI / uvicorn single event loop · token auth"]
    API --> LOOP["AgentLoop: build → route → stream → tool call → approval gate → execute → injection guard → backfill"]
    LOOP --> TOOLS["Tools: read/write(diff) · rg search · run_shell · apply_patch · index_project · plugins"]
    LOOP --> SUB["Sub-agents: explore (read-only) / general (gated)"]
    LOOP --> MEM["Memory: FTS5 + optional BGE-M3 / Doubao embeddings"]
    LOOP --> MCP["MCP: stdio + Streamable HTTP"]
    LOOP --> PTY["Persistent PTY terminal (WebSocket)"]
    LOOP --> USAGE["Usage: JSONL + cost dashboard"]
    TOOLS --> GATE["Approval gate · path boundary · protected paths"]
```

## 📁 Repo layout

```
spark/
  config.py       tomlkit config · chmod 600 · 2026 CN model presets · fast-model routing
  provider.py     OpenAI-compatible streaming client · demo mock · token estimate · summarizer
  approval.py     approval gate (path boundary always wins)
  loop.py         agent loop (event JSONL · context compaction · routing · injection guard)
  store.py        session JSONL (with delete)
  memory.py       long-term memory (SQLite + FTS5 + optional semantic)
  tui.py          Textual terminal UI
  tools/          fs / shell / plan / git checkpoint / memory / mcp / injection / registry
  web/            FastAPI + index.html (MCP mgmt · memory mgmt · sessions · brand empty state)
  cli.py          spark web / run / doctor / config / memory / tui
examples/plugins/ copy-ready sample plugins (time / text / loc tools)
tests/           pytest — approval / tools / loop / config / memory / checkpoints / web / MCP / injection / TUI
```

## ✅ Tests

```bash
python3 -m pytest -q      # 227 tests — approval rules, tools, loop, memory, injection,
                          # checkpoints, web API, MCP (stdio + HTTP), TUI
python3 tests/e2e_manual.py  # real-uvicorn E2E (in-stream approval / 409 / disk writes)
```

## 📚 Documentation (中文 / English)

| | 中文 | English |
| --- | --- | --- |
| Install & build (Windows EXE) | [docs/INSTALL.md](docs/INSTALL.md) | [docs/en/INSTALL.md](docs/en/INSTALL.md) |
| Configuration | [docs/CONFIG.md](docs/CONFIG.md) | [docs/en/CONFIG.md](docs/en/CONFIG.md) |
| HTTP API | [docs/API.md](docs/API.md) | [docs/en/API.md](docs/en/API.md) |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | [docs/en/ARCHITECTURE.md](docs/en/ARCHITECTURE.md) |

## 📄 License

MIT. Built on the shoulders of open source — ripgrep, difflib, SQLite FTS5, the official `mcp` SDK, Textual, FastAPI. No reinvented wheels, no SaaS lock-in.

---

**Made for people who like their agent to ask first, run locally, and stay out of their wallet.** Star it, fork it, plug your own tools in — Spark is yours.

> AI生成