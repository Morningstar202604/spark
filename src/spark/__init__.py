"""Spark — local-first AI coding agent."""

import os

# Skip LiteLLM's model-cost-map fetch from raw.githubusercontent.com, which is
# unreachable from mainland China and burns ~9s of connection retries on every
# process start. The bundled local map ships in the wheel; users can opt back
# into remote refreshes by setting the variable to False explicitly.
# Must run before the first litellm import anywhere in the package (litellm
# reads this variable at import time), so it lives at the package root —
# every entry point (cli/web/desktop) imports spark first.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def _resolve_version() -> str:
    """Single source of truth: the installed distribution metadata.

    A hardcoded literal drifts from pyproject.toml on every release (0.9.3 had
    to patch exactly this), and the ACP surface reports this value to editors.
    Falls back to the source tree's pyproject when the package is imported
    without being installed.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("spark-agent")
        except PackageNotFoundError:
            pass
    except Exception:  # pragma: no cover - importlib always present on 3.11+
        pass
    try:
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        with pyproject.open("rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except Exception:  # pragma: no cover - frozen/source-less deployments
        return "0.0.0"


__version__ = _resolve_version()
