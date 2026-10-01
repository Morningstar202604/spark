# Install & Build (Windows EXE / macOS / Linux)

## Option A — run from source (fastest)

```bash
git clone https://github.com/x33834/spark.git
cd spark
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark2 web                        # open the printed URL
```

## Option B — packaged binary

### Windows EXE

Local build (any Windows machine with Python 3.11+):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
# output: dist\spark\spark.exe
dist\spark\spark.exe web
```

Or grab the ready-made artifact from **GitHub Releases** — tag a version
(`git tag v0.9.0 && git push --tags`) and the `Release` workflow builds
`spark-windows-x86_64.zip` (plus Linux/macOS archives) automatically.

### Linux / macOS

```bash
bash scripts/build.sh            # output: dist/spark/spark
./dist/spark/spark web
```

### What the package contains

- `spark` executable — CLI + Web server (frontend assets embedded)
- Everything runs locally under `~/.spark2/`
- No runtime dependency on a browser other than opening `http://127.0.0.1:PORT`
- Optional extras (MCP / embedding / TUI) degrade gracefully when absent

## First run

1. Open the printed `http://127.0.0.1:PORT`
2. Settings → pick a model preset (DeepSeek / Qwen / GLM / Kimi / Doubao / Unisound / Ollama / custom), paste the API key
3. Point the working directory at your project
4. Create a session and start — every write and command is gated by approval

## Troubleshooting

- Port busy → `spark2 web --port 8788`
- Model errors → Settings → Test connection; check `base_url` / `model` / `api_key`
- Need a proxy → set `proxy` in config or `HTTPS_PROXY` env var
- Windows SmartScreen → click "More info → Run anyway" (unsigned build)
