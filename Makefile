# Spark 常用命令（轻量入口）
# 用法：make <target>；Windows 无 make 时用 .venv/bin/python -m 等价命令

PY := .venv/bin/python
PIP := .venv/bin/pip

.PHONY: install dev test lint build run doctor clean

## 安装
install:            ## 安装到 .venv（可编辑 + dev 依赖）
	python3 -m venv .venv
	$(PIP) install -e ".[dev]"

## 日常开发
dev:                ## 启动 Web 界面（工作目录 = 当前目录，本机免登录）
	$(PY) -m spark web --workdir .

test:               ## 全量测试
	$(PY) -m pytest -q

lint:               ## ruff 静态检查（未安装时自动安装）
	$(PIP) install -q ruff && .venv/bin/ruff check spark tests

build:              ## 构建 wheel（输出到 dist/）
	$(PIP) install -q build && $(PY) -m build

run:                ## 无头模式运行（输入任务描述）
	$(PY) -m spark run

doctor:             ## 环境自检（依赖/配置/工作目录）
	$(PY) -m spark doctor

## 清理
clean:              ## 删除构建与缓存产物
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
