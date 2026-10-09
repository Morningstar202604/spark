<div align="center">

# ⚡ Spark

**Asistente de programación con IA, local, que actúa solo con tu aprobación.**

Corre en tu máquina · cada paso visible · escrituras y comandos requieren tu visto bueno · cero llamadas ocultas a IA

[![License: MIT](https://img.shields.io/badge/license-MIT-14b8a6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-0f766e.svg)]()
[![Tests](https://img.shields.io/badge/tests-216%20passed-14b8a6.svg)]()

[English](README.md) · [中文](README.zh.md) · [日本語](README.ja.md) · **Español**

</div>

Spark es un **asistente de programación con IA de prioridad local**: sin Electron pesado, sin dependencia en la nube,
sin llamadas ocultas a modelos. Una **puerta de aprobación** protege tu proyecto (escribir archivos o ejecutar comandos
siempre requiere tu visto bueno), incluye **protección contra inyección de prompts**, memoria **100% local** y soporte
listo para usar de modelos chinos (DeepSeek / Qwen / GLM / Kimi / Doubao / Unisound / Ollama).

## Inicio rápido

```bash
cd spark
pip install -e ".[dev]"     # incluye uvicorn[standard] (necesario para la terminal Web)
spark web                   # abre la URL impresa (solo escucha 127.0.0.1)
```

## Características

- 🛡️ **Puerta de aprobación** — modos suggest / auto-edit / full-auto / plan
- 🌐 **Herramientas web** — `web_search` + `read_url` (solo lectura, sin aprobación)
- 🔁 **Conmutación por error** — `fallback_model` ante fallos del modelo principal; ruteo con `model_fast`
- ✏️ **Editar y reenviar** — edita cualquier mensaje, corta la sesión y reenvía; borrado persistente
- 🎙️ **Entrada por voz** — dictado en chino con Web Speech
- 🛒 **Mercado MCP** — instala servidores oficiales con un clic
- 🧠 **Memoria local** — memoria entre sesiones con FTS5 y búsqueda semántica opcional
- 🧩 **Plugins** — coloca un `.py` en `~/.spark/plugins/`

## Documentación

- `docs/CONFIG.md` — referencia completa de configuración
- `docs/API.md` — referencia de la API HTTP
- `docs/ARCHITECTURE.md` — arquitectura / flujo de datos / modelo de seguridad

## Licencia

MIT.

> AI生成
