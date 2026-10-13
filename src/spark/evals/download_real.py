"""可选的真实数据刷新器：从 HuggingFace datasets-server 拉取 SWE-bench Lite 行。

仅本地手动使用（``python -m spark.evals --refresh-real``）；CI 不联网，
直接使用本包内 ``data/swe_lite_real_subset.json`` 快照。

注意：τ-bench 的 HF 数据集（sierra-research/*）匿名访问返回 401（gated），
未拉取；fixture 以 τ-bench 轨迹风格手写（见 scenarios.py）。
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

DATASET = "princeton-nlp/SWE-bench_Lite"
URL = (
    "https://datasets-server.huggingface.co/first-rows"
    f"?dataset={DATASET.replace('/', '%2F')}&config=default&split=test"
)


def fetch_rows(limit: int = 8) -> list[dict]:
    req = urllib.request.Request(URL, headers={"User-Agent": "spark-eval-refresh"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    rows = [r["row"] for r in payload.get("rows", [])]
    out = []
    for r in rows[:limit]:
        out.append(
            {
                "instance_id": r["instance_id"],
                "repo": r["repo"],
                "problem_statement": r["problem_statement"],
                "fail_to_pass": r.get("FAIL_TO_PASS", ""),
                "pass_to_pass": r.get("PASS_TO_PASS", ""),
                "gold_patch": r.get("patch", ""),
                "test_patch": r.get("test_patch", ""),
            }
        )
    return out


def refresh(dest: Path | None = None, limit: int = 8) -> Path:
    if dest is None:
        from importlib import resources

        dest = Path(str(resources.files("spark.evals"))) / "data" / "swe_lite_real_subset.json"
    rows = fetch_rows(limit)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(f"refreshed {len(rows)} rows -> {dest} ({dest.stat().st_size} bytes)\n")
    return dest


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    refresh(limit=limit)
