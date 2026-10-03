# 贡献指南

欢迎为 Spark 贡献代码、文档、翻译或想法。保持小而美：**轻量、快速、开箱即用、国产模型优先**。

## 开发环境

```bash
git clone <你的 fork>
cd spark
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## 本地验证

每次提交前请保证：

```bash
ruff check spark tests          # 无 lint 错误
python -m pytest -q              # 216+ 用例全过
node --check spark/web/js/*.js  # 前端语法（改了前端时）
```

## 分支与提交

- 功能分支命名：`feat/xxx`、`fix/xxx`、`docs/xxx`
- 提交信息用中文或英文均可，写清楚"改了什么、为什么"
- 保持提交原子性：一个提交一件事

## 提 PR

1. Fork 并创建分支
2. 改动 + 测试（新功能必须带测试）
3. 走完上面「本地验证」
4. 提交 PR，用仓库内模板，关联对应 Issue

## 翻译

多语言 README 位于仓库根（`README.zh.md` / `README.ja.md` / `README.es.md`），
新增语言请复制任一版本翻译后命名 `README.<lang>.md`，并在根 `README.md` 的
语言切换徽章里登记。

## 代码风格

- Python 3.11+，类型注解齐全，遵循 ruff 默认规则
- 不引入重依赖：HTTP 用 httpx，UI 原生 Web Components + ES Modules（无构建）
- 安全优先：写操作一律过审批门；密钥不回显、不落日志

> AI生成
