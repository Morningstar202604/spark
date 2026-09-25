# Spark 示例插件（直接复制到 ~/.spark2/plugins/ 即可生效，重启后出现在设置→插件）

## 1. time_tool.py —— now 工具
返回当前日期时间，最简单的插件示范（工具列表写法 `tools = [...]`）。

## 2. text_tools.py —— slugify / base64 工具
文本处理小工具（`register(reg)` 写法）：
- slugify：把中文/空格标题转成 URL 友好 slug（写 README/文件名常用）
- b64：base64 编解码（配合终端/脚本调试）

## 3. repo_tools.py —— count_loc 工具
统计项目代码行数（只读，排除 .git/node_modules），适合"这个项目多大"类提问。

## 插件契约
- 每个 .py 是一个插件；导出 `tools`（列表）或 `register(reg)` 函数均可；
- 工具用 `spark2.tools.base.Tool` 定义：name / description / parameters(JSON Schema) / category(read|write|shell|system) / handler；
- **category 决定审批**：read 直接执行；write / shell 会在弹窗征求你确认——插件不会绕过审批门；
- 插件是本地代码，信任级与"内置终端/运行脚本"一致；加载失败只提示不中断服务。
