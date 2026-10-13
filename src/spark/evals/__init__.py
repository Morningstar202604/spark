"""spark.evals 包：fixture 驱动的 agent eval harness（P1-6）。"""

from spark.evals.harness import (
    JudgeResult,
    Scenario,
    ScenarioResult,
    load_real_swe_subset,
    run_report,
    run_scenario,
)
from spark.evals.scenarios import builtin_scenarios

__all__ = [
    "Scenario",
    "ScenarioResult",
    "JudgeResult",
    "builtin_scenarios",
    "load_real_swe_subset",
    "run_scenario",
    "run_report",
]
