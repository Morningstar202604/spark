"""``python -m spark.evals`` 入口。

用法：
    python -m spark.evals                     # 全部场景（内置 + 真实子集快照）
    python -m spark.evals --suite swe-lite-style
    python -m spark.evals --id swe-toy-calc-add
    python -m spark.evals --json report.json   # 汇总报告落盘
    python -m spark.evals --refresh-real       # 手动从 HF 刷新真实子集（需联网）
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from spark.evals.harness import load_real_swe_subset, run_report
from spark.evals.scenarios import builtin_scenarios


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spark.evals", description="agent eval harness (P1-6)")
    parser.add_argument("--suite", default="", help="逗号分隔的 suite 过滤")
    parser.add_argument("--id", default="", help="只跑指定 scenario id")
    parser.add_argument("--json", default="", help="汇总报告 JSON 输出路径")
    parser.add_argument("--workdir", default="", help="场景临时 workdir 根目录")
    parser.add_argument("--no-real", action="store_true", help="跳过 swe-lite-real 真实子集 smoke")
    parser.add_argument(
        "--refresh-real", action="store_true", help="从 HF 刷新真实子集快照（联网）"
    )
    args = parser.parse_args(argv)

    if args.refresh_real:
        from spark.evals.download_real import refresh

        refresh()
        return 0

    scenarios = builtin_scenarios()
    if not args.no_real:
        scenarios += load_real_swe_subset()
    if args.suite:
        want = {s.strip() for s in args.suite.split(",") if s.strip()}
        scenarios = [s for s in scenarios if s.suite in want]
    if args.id:
        scenarios = [s for s in scenarios if s.id == args.id]
    if not scenarios:
        sys.stderr.write("no scenarios matched\n")
        return 2

    workroot = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="spark-evals-"))
    report = run_report(scenarios, workroot)
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        sys.stdout.write(f"report written: {args.json}\n")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
