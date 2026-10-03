# -*- mode: python ; coding: utf-8 -*-
# Spark — PyInstaller spec
# 用法：
#   Windows:  pyinstaller spark.spec --distpath dist --workpath build
#   Linux/macOS 同命令（产出对应平台二进制）
# 构建说明见 docs/INSTALL.md（中英双语）

a = Analysis(
    ['spark/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('spark/web', 'spark/web'),          # 前端静态资源（index.html / js / css / vendor）
        ('assets/logo.svg', 'assets'),         # 品牌 logo
    ],
    hiddenimports=[
        # uvicorn 动态导入的子系统（[standard] 全家）
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.lifespan.on',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.protocols.websockets.ws_impl',
        'uvicorn.protocols.websockets.websockets_impl',
        'websockets',
        'httptools',
        # CLI / 配置 / 工具
        'typer',
        'tomlkit',
        'httpx',
        'anyio',
        'sniffio',
        'certifi',
        # Web Speech / MCP 等可选能力不硬依赖，缺失时优雅降级
        'spark.web',
        'spark.web.server',
        'spark.web.api_common',
        'spark.web.api_sessions',
        'spark.web.api_config',
        'spark.web.api_data',
        'spark.tools',
        'spark.tools.web',
        'spark.subagent',
        'spark.memory',
        'spark.codeindex',
        'spark.plugins',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'pytest',
        'pytest_asyncio',
        'PySide6',
        'PyQt5',
        'PyQt6',
        'IPython',
        'matplotlib',
        'numpy',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='spark',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,          # CLI + 可打印启动 URL
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/logo.ico' if os.path.exists('assets/logo.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='spark',
)
