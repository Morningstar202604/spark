> 🌐 [English](README.md) | [中文](README_zh.md)

# Spark — Local-first AI Coding Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen.svg)](https://www.python.org/)
[![Node 18+](https://img.shields.io/badge/node-18%2B-339933.svg)](https://nodejs.org/)

**Code stays with you. Keys stay with you.** A self-hosted AI coding assistant that runs entirely on your machine — zero privacy leaks, full control.

## Core Values

🔒 **Privacy First** — All code, conversation, and memory data stay entirely local. No third-party servers involved.  
⚡ **Four Form Factors** — Web UI, native desktop window, terminal TUI, and scriptable CLI.  
🧠 **Persistent Memory** — A vector memory store that remembers your project preferences, naming conventions, and workflow habits.  
🔬 **Four-Level Sandbox** — From read-only analysis to full access, tunable by risk profile.  
⏮️ **Checkpoint & Rollback** — Workspace snapshots plus conversation restore — jump to any previous state like Git.

## Preview

> Every screenshot below was captured from a **live session against a real
> model** (`Qwen3.8-Flash-Next` via an OpenAI-compatible endpoint) running on a
> real workdir — no mock, no mock-up.

### Chat · reasoning, tool call, answer

> The model read `greet.py`, reasoned about it, and answered in one sentence.
> The `read_file` card shows **✓ Completed** — this is the actual tool round-trip,
> streamed over SSE with a live context-usage meter in the header.

![Chat](assets/screenshots/11-chat.png)

### Landing · pick up where you left off

> Session sidebar grouped by recency, one-click new session, searchable history,
> and the model/approval/sandbox state always visible in the footer.

![Empty state](assets/screenshots/10-empty-state.png)

### Settings · models

> Any OpenAI-compatible endpoint (DeepSeek / Qwen / GLM / Kimi / Doubao / Ollama /
> custom), saved model profiles with one-click switching, a `Test connection`
> probe, and three reasoning-effort levels mapped to provider-native parameters.

![Settings · Model](assets/screenshots/12-settings-model.png)

### Settings · permissions and sandbox

> Four access levels — sandbox-only / workspace (recommended) / full-access /
> unrestricted — each with an explicit behaviour description. The protected-path
> list is user-editable; `.git/hooks`, `~/.spark` and Spark's own source are
> always protected.

![Settings · Permissions](assets/screenshots/13-settings-permissions.png)

### Command palette · Ctrl+K

> Fuzzy-jump to every surface: new session, checkpoints, turn replay, file
> viewer, web reader, theme, language.

![Command palette](assets/screenshots/15-command-palette.png)

### File viewer · syntax highlighting

> Browse the workdir and preview files with line numbers and highlight.js
> colouring, plus graceful handling of binary and oversized files.

![File viewer](assets/screenshots/16-file-viewer.png)

### Responsive · desktop and phone

> The same UI at 390 px: the sidebar collapses to a drawer, settings tabs turn
> into a horizontal scroller, and the composer stays reachable. On desktop the
> sidebar width is drag-adjustable and persisted.

![Mobile settings](assets/screenshots/18-mobile-settings.png)

## Quick Start

```bash
git clone https://gitcode.com/badhope/spark        # 或 GitHub: https://github.com/badhope/spark
cd spark && python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark web --workdir /your/project
```

Open the printed address in your browser. **Without an API key Spark starts in demo mode (mock);
paste a key into the web settings and it switches to the real model immediately — no restart.**

Windows users can also build a portable EXE (Python 3.11+):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
dist\spark\spark.exe web                    # or just double-click dist\spark.exe
```

## Four Form Factors

| Form | Command | Use case |
|------|---------|----------|
| Web UI | `spark web` | Everyday chat, file viewer, terminal, execution graph (auto-picks a free port) |
| Desktop | `spark desktop` | Native window via pywebview, bound to loopback |
| Terminal TUI | `spark` | Pure terminal environments, remote SSH |
| CLI / scripts | `spark exec "task"` | Automation, CI, piping |
| IDE (ACP) | `spark acp` | Run as an ACP agent inside Zed and other editors (see [docs/ACP.md](docs/ACP.md)) |
| MCP server | `spark mcp-serve` | Expose every built-in tool to other MCP clients |

## Feature Overview

| Category | Features |
|----------|----------|
| Chat | SSE streaming, Markdown code blocks, image attachments, reasoning stream display |
| Tools | File read/write, apply_edit with AST syntax gate, grep/glob, shell, background jobs, Jupyter, web search/fetch, full Git suite, worktrees |
| Planning | Multi-step plans (PlanCard visualization), parallel sub-agent delegation (up to 4), declarative sub-agents (`.claude/agents/*.md`) |
| Approval | Six approval modes (incl. plan-only / approve-plan), `[[permissions]]` wildcard rule engine, three-way approval dialog |
| Permissions | Four-level sandbox, protected paths, credential-path protection, token-level shell checks, MCP least-privilege allowlists |
| Memory | AGENTS.md injection, vector long-term memory (embeddings + optional reranker), auto-extraction, web-based management |
| Knowledge | Project knowledge base: import docs, BM25 retrieval injected per turn as read-only reference (no extra vector store) |
| Persistence | Sessions, checkpoint rollback (works without git), hot config reload, token usage stats |
| Models | Any OpenAI-compatible endpoint, local Ollama, multi-profile switching, `spark test` probe |
| Extension | MCP client (stdio/SSE/HTTP + OAuth), MCP registry search with one-click add for both stdio and remote servers, skills `.spark/skills/`, slash-command templates, plugins, lifecycle hooks |
| UI | Chinese/English, light/dark themes, command palette (Ctrl+K), rebindable shortcuts, file viewer, built-in terminal, web reader, execution graph, checkpoint panel, full-text session search |

## Architecture

![Architecture Overview](docs/assets/architecture.svg)

Detailed architecture → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Platform Requirements

- Python 3.11+
- Node.js 18+ (frontend development only; the bundled frontend ships prebuilt)
- Any OpenAI-compatible API (DeepSeek, OpenAI, Ollama, Azure, Agnes, Unisound, SenseNova, …)
- Windows / macOS / Linux (one-click EXE build on Windows)

## Documentation

| Document | Contents |
|----------|----------|
| [docs/INSTALL.md](docs/INSTALL.md) | Install & packaging (incl. Windows EXE) |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Configuration reference (config.toml / env vars / API overview) |
| [docs/API.md](docs/API.md) | HTTP API and SSE event protocol |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture notes |
| [docs/competitive-comparison.md](docs/competitive-comparison.md) | Feature comparison vs Qoder / ZCode / opencode / Codex CLI |

## Community

- Contributing: [CONTRIBUTING.md](CONTRIBUTING.md)
- Issue Tracker: use the **Issues** tab of this repository

---

**License:** MIT · **Author:** badhope · **Built with ❤️ in Shenzhen**
