<div align="center">

# ⚡ Spark

**あなたのマシンで完結する、ローカル自ホスト型 AI コーディングエージェント。**

コードはあなたの手元に · キーはあなたの手元に · 承認ゲート付き · クラウド依存ゼロ

[![License: MIT](https://img.shields.io/badge/license-MIT-14b8a6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-0f766e.svg)]()
[![Tests](https://img.shields.io/badge/tests-626%20passed-14b8a6.svg)]()

[English](README.md) · [中文](README_zh.md) · **日本語** · [Español](README.es.md)

</div>

Spark は**ローカル優先**の AI プログラミングアシスタントです。すべてのコード・会話・記憶は
ローカルに保存され、第三者サーバーを経由しません。**承認ゲート**（6 段階の承認モード）と
**4 段階サンドボックス**がプロジェクトを保護し、**Prompt Injection 対策**（untrusted フェンス）を内蔵。
DeepSeek / 通義 / 智譜 / Kimi / 豆包 / Agnes AI / Ollama / 任意の OpenAI 互換 API がすぐ使えます。

## 画面プレビュー

> 実際のモデル（Agnes 3.0 Flash）によるセッション。モデルが `read_file` ツールを呼び出し、
> `pyproject.toml` を読んでプロジェクトを要約しています。ツールカードは ✓ 完了 表示。

![チャット画面](assets/screenshots/11-chat.png)

> ファイルビューア：行番号付きシンタックスハイライト（複数行トークンの着色も正しい）。

![ファイルビューア](assets/screenshots/16-file-viewer.png)

> Windows EXE（PyInstaller 単一ファイル、Python 不要）。モデルが `run_shell` を実行し、
> 出力 `42` を読み取って終了コードを報告。

![EXE 実行](assets/screenshots/15-command-palette.png)

<details>
<summary>さらに表示（設定パネル / 英語 UI）</summary>

![設定 · モデル](assets/screenshots/12-settings-model.png)

![設定 · 権限](assets/screenshots/13-settings-permissions.png)

![設定 · 表示](assets/screenshots/12-settings-model.png)

![英語 UI](assets/screenshots/10-empty-state.png)

</details>

## クイックスタート

```bash
git clone https://gitcode.com/badhope/spark        # または GitHub: https://github.com/badhope/spark
cd spark && python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark web --workdir /your/project
```

API キー未設定でも**デモモード（mock）で起動**し、Web 設定でキーを貼ると即座に実モデルへ切替（再起動不要）。

Windows なら EXE もビルド可能：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
dist\spark\spark.exe web
```

## 4 つの形態

| 形態 | コマンド | 用途 |
|------|----------|------|
| Web UI | `spark web` | 日常の対話・ファイル閲覧・ターミナル・実行グラフ（空きポートを自動選択） |
| デスクトップ | `spark desktop` | ネイティブウィンドウ（pywebview） |
| ターミナル TUI | `spark` | 純ターミナル環境・SSH リモート |
| CLI / スクリプト | `spark exec "タスク"` | 自動化・CI・パイプ |
| MCP サーバー | `spark mcp-serve` | 内蔵ツールを他の MCP クライアントへ公開 |

## 主な機能

- 🛡️ **承認ゲート** — 6 段階（suggest / auto-edit / full-auto / plan-only / approve-plan / manual）+ `[[permissions]]` ワイルドカード規則
- 🔬 **4 段階サンドボックス** — sandbox-only / workspace / full-access / unrestricted、保護パス・認証情報パス保護
- 🧰 **豊富なツール** — ファイル読み書き、apply_edit（AST 構文ゲート）、grep/glob、Shell、バックグラウンドジョブ、Jupyter、Web 検索/取得、Git 一式、worktree
- 🧠 **長期記憶** — ベクトル記憶 + 自動抽出 + Web 管理画面、AGENTS.md 注入
- ⏮️ **チェックポイント** — tar.gz スナップショット、git 不要のロールバック
- 🌐 **Web UI** — 中英バイリンガル、ダーク/ライトテーマ、コマンドパレット（Ctrl+K）、ファイルビューア、内蔵ターミナル、Web リーダー、実行グラフ
- 🔌 **MCP** — クライアント（stdio/SSE/HTTP + OAuth）とサーバー両対応、レジストリ検索（中国での可用性評価付き）
- 🧩 **プロジェクトスキル** — `.spark/skills/` に Markdown を置くだけ、`load_skill` で読み込み
- 🪝 **ライフサイクル hooks** — pre_tool でブロック可能（`on_deny=block` が既定）

## ドキュメント

- `docs/INSTALL.md` — インストールとパッケージング（Windows EXE 含む）
- `docs/CONFIG.md` — 設定ファイル全項目
- `docs/API.md` — HTTP API / SSE イベントプロトコル
- `docs/ARCHITECTURE.md` — アーキテクチャ / データフロー / セキュリティモデル

## ライセンス

MIT。
