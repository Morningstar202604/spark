# 安装与打包（Windows EXE / macOS / Linux）

## 方式 A — 源码运行（最快）

```bash
git clone https://gitcode.com/badhope/spark.git
cd spark
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark web                        # 打开打印出的地址
```

## 方式 B — 打包版二进制

### Windows EXE

任意 Windows 机器（Python 3.11+）本地一键构建：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
# 产物：dist\spark\spark.exe
dist\spark\spark.exe web
```

或直接下载 **GitHub Releases** 现成包：打个 tag（`git tag v0.9.0 && git push --tags`），
`Release` 工作流会自动构建 `spark-windows-x86_64.zip`（同时产出 Linux/macOS 压缩包）。

### Linux / macOS

```bash
bash scripts/build.sh            # 产物：dist/spark/spark
./dist/spark/spark web
```

### 桌面版（原生窗口）

另有一份 GUI 产物，双击直接开窗口，不经过终端：

```powershell
pyinstaller spark-desktop.spec --distpath dist --workpath build
# 产物：dist\spark-desktop\spark-desktop.exe
```

两者的区别：

| | `spark.exe` | `spark-desktop.exe` |
|---|---|---|
| Windows 子系统 | Console（3） | GUI（2，无黑框） |
| 双击行为 | 进 TUI | 直接开原生窗口 |
| 入口模块 | `spark.__main__` | `spark.desktop_main` |
| 适用 | 命令行 / 服务器 / 脚本 | 普通用户日常使用 |

桌面版启动失败时会写 `~/.spark/desktop-crash.log`（GUI 子系统没有 stderr
可看，不写日志的话用户只会觉得「点了没反应」）。

### 打包内容

- `spark` 可执行文件 —— CLI + Web 服务（前端资源已内嵌）
- `spark-desktop` 可执行文件 —— 同一套后端 + pywebview 原生窗口
- 全部数据本地化，运行于 `~/.spark/`
- 无需额外浏览器依赖，浏览器打开打印的 `http://127.0.0.1:端口` 即可（服务默认仅监听 `127.0.0.1`（本机访问；如需外部预览请显式 `spark web --host 0.0.0.0`，并自行承担暴露风险））
- 可选能力（MCP / 嵌入 / TUI）缺失时优雅降级

## 首次使用

1. 打开打印的 `http://127.0.0.1:端口`（默认仅本机可访问；外部预览需显式 `--host 0.0.0.0`）
2. 设置 → 选模型预设（DeepSeek / 通义 / 智谱 / Kimi / 豆包 / Agnes AI / Ollama / 自定义），粘贴 API Key
3. 把「工作目录」指向你的项目
4. 新建会话开干 —— 每次写入与命令都在审批门管控下

## 常见问题

- 端口被占 → `spark web --port 8788`
- 模型报错 → 设置面板「测试连接」；检查 `base_url` / `model` / `api_key`
- 需要代理 → 配置 `proxy` 或设 `HTTPS_PROXY` 环境变量
- Windows SmartScreen 提示 → 「更多信息 → 仍要运行」（未签名构建）

> AI生成
