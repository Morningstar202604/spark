<div align="center">

# ⚡ Spark

**あなたの同意を得てから動く、ローカル AI プログラミングアシスタント。**

ローカル実行 · 全ステップ可視 · 書き込みとコマンドは要承認 · 隠れた AI 呼び出しゼロ

[![License: MIT](https://img.shields.io/badge/license-MIT-14b8a6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-0f766e.svg)]()
[![Tests](https://img.shields.io/badge/tests-216%20passed-14b8a6.svg)]()

[English](README.md) · [中文](README.zh.md) · **日本語** · [Español](README.es.md)

</div>

Spark は**ローカル優先**の AI プログラミングアシスタントです。Electron の肥大化なし、クラウド依存なし、
隠れたモデル呼び出しなし。**承認ゲート**がプロジェクトを保護し（ファイル書き込み・コマンド実行は必ず承認）、
**Prompt Injection 対策**を内蔵、記憶は**すべてローカル**、国産モデル（DeepSeek / 通義 / 智譜 / Kimi / 豆包 / 云知声 / Ollama）がすぐ使えます。

## クイックスタート

```bash
cd spark
pip install -e ".[dev]"     # 依存に uvicorn[standard] を含む（Web ターミナル WS 用）
spark2 web                   # 表示された URL を開く（127.0.0.1 のみ）
```

## 主な機能

- 🛡️ **承認ゲート** — 書き込み・コマンドを承認モード（suggest / auto-edit / full-auto / plan）で制御
- 🌐 **Web ツール** — `web_search` + `read_url`（読み取り専用、承認不要）
- 🔁 **モデル冗長化** — `fallback_model` で障害自動切替、`model_fast` でルーティング
- ✏️ **メッセージ編集・再送** — 編集→原文読込→会話切断→再送；削除は永続
- 🎙️ **音声入力** — Web Speech 中国語ディクテーション
- 🛒 **MCP マーケット** — 公式サーバーをワンクリック導入
- 🧠 **ローカル記憶** — クロスセッション記憶、FTS5 + 任意の意味検索
- 🧩 **プラグイン** — `~/.spark2/plugins/` に .py を置くだけ

## ドキュメント

- `docs/CONFIG.md` — 設定ファイル全項目
- `docs/API.md` — HTTP API リファレンス
- `docs/ARCHITECTURE.md` — アーキテクチャ / データフロー / セキュリティモデル

## ライセンス

MIT。
