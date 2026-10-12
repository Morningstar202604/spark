# Spark 示例

本目录提供可运行的配置与扩展示例。

## config.toml.example — 配置模板

复制到 `~/.spark/config.toml`（或运行 `spark init` 自动生成），字段说明见
[docs/CONFIG.md](../docs/CONFIG.md)，字段唯一事实来源是 `src/spark/config.py`。

## plugins/ — 工具扩展示例（P1 能力，待回迁）

> ⚠️ 当前 `src/spark` 主干**没有** `~/.spark/plugins/` 动态插件加载器
> （见 `docs/CONSOLIDATION-PLAN.md` 第 3 节 P2 行）。以下示例面向根树时代的
> `spark.tools.base.Tool` 契约，**现版本无法加载**，仅作为插件能力回迁时的
> 设计参考保留：每个 `.py` 导出 `tools` 列表或 `register(reg)` 函数，
> category 决定审批（read 免审批，write/shell 弹窗）。

- `time_tool.py` — 最小的 now 工具（列表写法）
- `text_tools.py` — slugify / base64 工具（register 写法）
- `repo_tools.py` — 统计代码行数（只读）

## skills/ — 项目级技能示例（当前版本可用）

`.spark/skills/` 是**当前版本已支持**的扩展点：把带 YAML frontmatter 的
Markdown 放进工作目录的 `.spark/skills/`，Agent 的系统提示词会出现
`<available_skills>` 列表，模型可用 `load_skill` 工具加载全文。

试用方法：

```bash
mkdir -p your-project/.spark/skills
cp examples/skills/refactor.md your-project/.spark/skills/
# 然后在 your-project 里启动 spark，问一句"帮我重构 xxx"即可看到技能生效
```
