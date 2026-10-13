"""P1-6 evals harness 单测：fixtures 驱动、不依赖网络。"""

from __future__ import annotations

from pathlib import Path

import spark.evals.__main__ as evals_main
from spark.evals import builtin_scenarios, load_real_swe_subset, run_scenario
from spark.evals.harness import Scenario


def test_builtin_scenarios_all_pass(tmp_path: Path) -> None:
    for i, scenario in enumerate(builtin_scenarios()):
        wd = tmp_path / f"s{i}"
        wd.mkdir()
        result = run_scenario(scenario, wd)
        assert result.passed, (
            f"{scenario.id} failed: {[j.detail for j in result.judges if not j.passed]}"
        )


def test_real_swe_subset_smoke(tmp_path: Path) -> None:
    scenarios = load_real_swe_subset()
    assert len(scenarios) == 8
    ids = {s.id for s in scenarios}
    assert len(ids) == 8  # instance_id 唯一
    assert all(s.suite == "swe-lite-real" for s in scenarios)
    # 元数据字段来自真实 HF 行（gold patch 仅作参考，不参与判定）
    sample = scenarios[0]
    assert sample.metadata["repo"]
    assert sample.metadata["gold_patch_chars"] > 100
    wd = tmp_path / "real"
    wd.mkdir()
    result = run_scenario(sample, wd)
    assert result.passed, [j.detail for j in result.judges]


def test_failing_scenario_is_detected(tmp_path: Path) -> None:
    """缺失文件的判定器必须被 harness 判负（而不是假 pass）。"""
    broken = Scenario(
        id="unit-broken",
        suite="unit",
        prompt="write a file please",
        script=[
            [{"type": "text", "text": "done"}],
        ],
        judges=[{"type": "files_exist", "paths": ["missing.txt"]}],
    )
    wd = tmp_path / "broken"
    wd.mkdir()
    result = run_scenario(broken, wd)
    assert not result.passed
    assert any(j.name == "files_exist" and not j.passed for j in result.judges)


def test_pytest_judge_catches_unfixed_bug(tmp_path: Path) -> None:
    """SWE 风格：bug 未修时 FAIL_TO_PASS 必须真的红。"""
    unfixed = Scenario(
        id="unit-unfixed",
        suite="unit",
        prompt="please fix",
        setup_files={
            "calc.py": "def add(a, b):\n    return a - b\n",
            "test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
        script=[
            [{"type": "text", "text": "claimed fixed"}],
        ],
        judges=[
            {
                "type": "run_command",
                "command": "{python} -m pytest -q test_calc.py",
                "expect_returncode": 0,
            }
        ],
    )
    wd = tmp_path / "unfixed"
    wd.mkdir()
    result = run_scenario(unfixed, wd)
    assert not result.passed
    assert any(j.name == "run_command" and not j.passed for j in result.judges)


def test_tool_sequence_judge_enforces_order(tmp_path: Path) -> None:
    """τ-bench 风格：跳过 read_file 直接写日志，轨迹判定必须失败。"""
    skipped = Scenario(
        id="unit-skipped-read",
        suite="unit",
        prompt="refund ORD-1",
        setup_files={"orders.json": "{}"},
        script=[
            [
                {
                    "type": "tool_call",
                    "tool_call": {
                        "id": "c1",
                        "name": "write_file",
                        "arguments": {"path": "refund_log.json", "content": "{}"},
                    },
                }
            ],
            [{"type": "text", "text": "refunded"}],
        ],
        judges=[
            {
                "type": "tool_sequence",
                "calls": [
                    {"name": "read_file", "args_contains": {"path": "orders.json"}},
                    {"name": "write_file", "args_contains": {"path": "refund_log.json"}},
                ],
            }
        ],
    )
    wd = tmp_path / "skipped"
    wd.mkdir()
    result = run_scenario(skipped, wd)
    assert not result.passed
    assert any(j.name == "tool_sequence" and not j.passed for j in result.judges)


def test_cli_exit_code_zero_on_good(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(evals_main, "builtin_scenarios", lambda: builtin_scenarios()[:1])
    monkeypatch.setattr(evals_main, "load_real_swe_subset", lambda: [])
    rc = evals_main.main(["--no-real", "--workdir", str(tmp_path / "cli-ok")])
    assert rc == 0


def test_cli_exit_code_one_on_failure(monkeypatch, tmp_path: Path) -> None:
    broken = Scenario(
        id="cli-broken",
        suite="unit",
        prompt="x",
        script=[
            [{"type": "text", "text": "x"}],
        ],
        judges=[{"type": "files_exist", "paths": ["nope.txt"]}],
    )
    monkeypatch.setattr(evals_main, "builtin_scenarios", lambda: [broken])
    monkeypatch.setattr(evals_main, "load_real_swe_subset", lambda: [])
    rc = evals_main.main(["--no-real", "--workdir", str(tmp_path / "cli-bad")])
    assert rc == 1
