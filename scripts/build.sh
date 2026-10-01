#!/usr/bin/env bash
# Spark 构建脚本（Linux / macOS 二进制）
set -e
cd "$(dirname "$0")/.."
echo "[1/3] venv + deps"
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]" pyinstaller
echo "[2/3] tests"
.venv/bin/python -m pytest -q
echo "[3/3] pyinstaller"
.venv/bin/pyinstaller --clean -y spark.spec
echo "完成：dist/spark/spark（运行 dist/spark/spark web）"
