"""Health endpoints: ``GET /api/diagnostics`` + ``POST /api/selfheal``.

Thin HTTP layer over :mod:`spark.selfheal` so the web UI can show the same
health report the CLI prints and repair the safe subset with one click.
"""

from __future__ import annotations

from spark import audit, selfheal
from spark.web._httpbase import HandlerBase
from spark.web._routing import _route_state


def _handle_diagnostics(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    from spark.config import default_config_path

    rows = selfheal.diagnose(
        state.cfg, workdir=state.workdir, config_path=default_config_path()
    )
    handler._json(
        200,
        {
            "ok": True,
            "rows": rows,
            "blocking": sum(1 for r in rows if r["level"] == "bad"),
            "warnings": sum(1 for r in rows if r["level"] == "warn"),
            # Only present when SPARK_AUDIT_LOG is enabled; [] otherwise.
            "audit": audit.read_recent(limit=20),
        },
    )


def _handle_selfheal(handler: HandlerBase, body: dict) -> None:
    state = _route_state(handler)
    if state.busy:
        handler._json(409, {"error": "agent is busy"})
        return
    from spark.config import default_config_path

    report = selfheal.repair(
        state.cfg, workdir=state.workdir, config_path=default_config_path()
    )
    handler._json(200, report)
