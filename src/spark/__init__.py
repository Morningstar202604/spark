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

__version__ = "0.9.8"
