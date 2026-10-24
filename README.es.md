<div align="center">

# ⚡ Spark

**Agente de programación con IA, auto-alojado y 100% local.**

Tu código se queda contigo · tus claves se quedan contigo · puerta de aprobación · cero dependencia de la nube

[![License: MIT](https://img.shields.io/badge/license-MIT-14b8a6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-0f766e.svg)]()
[![Tests](https://img.shields.io/badge/tests-626%20passed-14b8a6.svg)]()

[English](README.md) · [中文](README_zh.md) · [日本語](README.ja.md) · **Español**

</div>

Spark es un **asistente de programación con IA de prioridad local**: sin Electron pesado, sin dependencia en la nube,
sin llamadas ocultas a modelos. Todo el código, las conversaciones y la memoria se guardan localmente y nunca pasan por
servidores de terceros. Una **puerta de aprobación** (6 modos) y un **sandbox de 4 niveles** protegen tu proyecto, con
**protección contra inyección de prompts** integrada. Funciona con cualquier API compatible con OpenAI
(DeepSeek / Qwen / GLM / Kimi / Doubao / Agnes AI / Ollama / …).

## Vista previa

> Sesión real con un modelo real (Agnes 3.0 Flash): el modelo llamó a la herramienta `read_file`,
> leyó `pyproject.toml` y resumió el proyecto. La tarjeta de herramienta muestra ✓ Completado.

![Chat](assets/screenshots/11-chat.png)

> Visor de archivos con numeración de líneas y resaltado de sintaxis (los tokens multilínea también se colorean bien).

![Visor de archivos](assets/screenshots/16-file-viewer.png)

> EXE de Windows (un solo archivo con PyInstaller, sin Python). El modelo ejecutó `run_shell`,
> leyó la salida `42` e informó el código de salida.

![EXE](assets/screenshots/15-command-palette.png)

<details>
<summary>Más capturas (ajustes / interfaz en inglés)</summary>

![Ajustes · Modelo](assets/screenshots/12-settings-model.png)

![Ajustes · Permisos](assets/screenshots/13-settings-permissions.png)

![Ajustes · Pantalla](assets/screenshots/12-settings-model.png)

![Interfaz en inglés](assets/screenshots/10-empty-state.png)

</details>

## Inicio rápido

```bash
git clone https://gitcode.com/badhope/spark        # o GitHub: https://github.com/badhope/spark
cd spark && python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
spark web --workdir /your/project
```

Sin clave de API, Spark arranca en **modo demo (mock)**; pega una clave en los ajustes web y cambia al modelo real
de inmediato, sin reiniciar.

En Windows también puedes compilar un EXE portable:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-windows.ps1
dist\spark\spark.exe web
```

## Cuatro formas

| Forma | Comando | Uso |
|-------|---------|-----|
| Web UI | `spark web` | Uso diario: chat, visor de archivos, terminal, grafo de ejecución (elige puerto libre automáticamente) |
| Escritorio | `spark desktop` | Ventana nativa (pywebview), ligada a loopback |
| TUI | `spark` | Entornos de terminal puros, SSH remoto |
| CLI / scripts | `spark exec "tarea"` | Automatización, CI, tuberías |
| Servidor MCP | `spark mcp-serve` | Expone todas las herramientas integradas a otros clientes MCP |

## Características

- 🛡️ **Puerta de aprobación** — 6 modos (suggest / auto-edit / full-auto / plan-only / approve-plan / manual) + reglas comodín `[[permissions]]`
- 🔬 **Sandbox de 4 niveles** — sandbox-only / workspace / full-access / unrestricted, con rutas protegidas y protección de credenciales
- 🧰 **Herramientas** — lectura/escritura, apply_edit (con puerta de sintaxis AST), grep/glob, shell, trabajos en segundo plano, Jupyter, búsqueda/lectura web, suite Git completa, worktrees
- 🧠 **Memoria a largo plazo** — almacén vectorial con extracción automática y gestión desde la web; inyección de AGENTS.md
- ⏮️ **Puntos de control** — instantáneas tar.gz y reversión sin necesidad de git
- 🌐 **Web UI** — interfaz chino/inglés, temas claro/oscuro, paleta de comandos (Ctrl+K), visor de archivos, terminal integrada, lector web, grafo de ejecución
- 🔌 **MCP** — cliente (stdio/SSE/HTTP + OAuth) y servidor, con búsqueda en el registro y evaluación de disponibilidad en China
- 🧩 **Skills de proyecto** — coloca Markdown en `.spark/skills/` y el modelo lo carga con `load_skill`
- 🪝 **Hooks de ciclo de vida** — `pre_tool` puede bloquear (`on_deny=block` es el valor por defecto)

## Documentación

- `docs/INSTALL.md` — instalación y empaquetado (incluye EXE de Windows)
- `docs/CONFIG.md` — referencia completa de configuración
- `docs/API.md` — API HTTP y protocolo de eventos SSE
- `docs/ARCHITECTURE.md` — arquitectura / flujo de datos / modelo de seguridad

## Licencia

MIT.
