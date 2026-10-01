# 安装与打包（Windows EXE / macOS / Linux）

## 方式 A — 源码运行（最快）

```bash
git clone https://github.com/x33834/spark.git
cd spark
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark2 web                        # 打开打印出的地址
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

### 打包内容

- `spark` 可执行文件 —— CLI + Web 服务（前端资源已内嵌）
- 全部数据本地化，运行于 `~/.spark2/`
- 无需额外浏览器依赖，浏览器打开 `http://127.0.0.1:端口` 即可
- 可选能力（MCP / 嵌入 / TUI）缺失时优雅降级

## 首次使用

1. 打开打印的 `http://127.0.0.1:端口`
2. 设置 → 选模型预设（DeepSeek / 通义 / 智谱 / Kimi / 豆包 / 云知声 / Ollama / 自定义），粘贴 API Key
3. 把「工作目录」指向你的项目
4. 新建会话开干 —— 每次写入与命令都在审批门管控下

## 常见问题

- 端口被占 → `spark2 web --port 8788`
- 模型报错 → 设置面板「测试连接」；检查 `base_url` / `model` / `api_key`
- 需要代理 → 配置 `proxy` 或设 `HTTPS_PROXY` 环境变量
- Windows SmartScreen 提示 → 「更多信息 → 仍要运行」（未签名构建）
