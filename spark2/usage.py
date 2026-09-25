"""用量与成本统计（P3 ⑦）：按会话累计 tokens 与估算费用。

- 零依赖：JSONL 追加写，进程内聚合；
- 单价表内置 2026-09 已查证的官方价格（标注口径与信源），估算 = tokens × 单价；
- 用户可在配置里覆盖单价（`usage_pricing` 覆盖项）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# 2026-09 现役官方价（元 / 百万 tokens）。口径：
# - deepseek-v4-pro / deepseek-v4-flash：DeepSeek 官方 api-docs（2026-08-17 峰谷定价生效，取"空闲时段"价）
# - doubao-1-5-pro-32k：火山方舟官方模型页（输入 0.8 / 输出 2.0）
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-pro": {"input": 4.5, "output": 13.5},
    "deepseek-v4-flash": {"input": 1.0, "output": 4.0},
    "doubao-1-5-pro-32k": {"input": 0.8, "output": 2.0},
    # 未列出的模型按"低单价兜底"估算，避免误报天价；可在设置里覆盖
    "_fallback": {"input": 1.0, "output": 4.0},
}


def _match_pricing(model: str, pricing: dict[str, dict[str, float]]) -> dict[str, float]:
    if model in pricing:
        return pricing[model]
    for prefix, p in pricing.items():
        if prefix != "_fallback" and model.startswith(prefix):
            return p
    return pricing.get("_fallback", DEFAULT_PRICING["_fallback"])


class UsageStore:
    def __init__(self, root: Path, pricing: dict[str, dict[str, float]] | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.pricing = dict(DEFAULT_PRICING)
        if pricing:
            self.pricing.update(pricing)

    def _path(self, day: str) -> Path:
        return self.root / f"{day}.jsonl"

    def record(
        self,
        session_id: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> dict:
        p = _match_pricing(model, self.pricing)
        est_cost = (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000
        rec = {
            "ts": time.time(),
            "session_id": session_id,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "est_cost": round(est_cost, 6),
        }
        import datetime

        day = datetime.date.today().isoformat()
        with self._path(day).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    def session_summary(self, session_id: str, days: int = 90) -> dict:
        """单会话聚合：tokens / 估算费用 / 按模型拆分。"""
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "est_cost": 0.0, "calls": 0}
        by_model: dict[str, dict] = {}
        import datetime

        today = datetime.date.today()
        for i in range(days):
            day = (today - datetime.timedelta(days=i)).isoformat()
            p = self._path(day)
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("session_id") != session_id:
                    continue
                totals["prompt_tokens"] += int(rec.get("prompt_tokens", 0))
                totals["completion_tokens"] += int(rec.get("completion_tokens", 0))
                totals["est_cost"] += float(rec.get("est_cost", 0))
                totals["calls"] += 1
                m = rec.get("model", "unknown")
                bm = by_model.setdefault(m, {"prompt_tokens": 0, "completion_tokens": 0, "est_cost": 0.0, "calls": 0})
                bm["prompt_tokens"] += int(rec.get("prompt_tokens", 0))
                bm["completion_tokens"] += int(rec.get("completion_tokens", 0))
                bm["est_cost"] += float(rec.get("est_cost", 0))
                bm["calls"] += 1
        totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
        totals["est_cost"] = round(totals["est_cost"], 4)
        for bm in by_model.values():
            bm["est_cost"] = round(bm["est_cost"], 4)
        return {"session_id": session_id, "totals": totals, "by_model": by_model}

    def global_summary(self, days: int = 30) -> dict:
        """全部会话聚合（成本面板总览）。"""
        totals = {"prompt_tokens": 0, "completion_tokens": 0, "est_cost": 0.0, "calls": 0}
        by_session: dict[str, dict] = {}
        import datetime

        today = datetime.date.today()
        for i in range(days):
            day = (today - datetime.timedelta(days=i)).isoformat()
            p = self._path(day)
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                totals["prompt_tokens"] += int(rec.get("prompt_tokens", 0))
                totals["completion_tokens"] += int(rec.get("completion_tokens", 0))
                totals["est_cost"] += float(rec.get("est_cost", 0))
                totals["calls"] += 1
                s = rec.get("session_id", "?")
                bs = by_session.setdefault(s, {"prompt_tokens": 0, "completion_tokens": 0, "est_cost": 0.0, "calls": 0})
                bs["prompt_tokens"] += int(rec.get("prompt_tokens", 0))
                bs["completion_tokens"] += int(rec.get("completion_tokens", 0))
                bs["est_cost"] += float(rec.get("est_cost", 0))
                bs["calls"] += 1
        totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
        totals["est_cost"] = round(totals["est_cost"], 4)
        ranked = sorted(
            [{"session_id": k, **v} for k, v in by_session.items()],
            key=lambda x: x["est_cost"],
            reverse=True,
        )[:10]
        for bs in ranked:
            bs["est_cost"] = round(bs["est_cost"], 4)
            bs["total_tokens"] = int(bs.get("prompt_tokens", 0)) + int(bs.get("completion_tokens", 0))
        return {"totals": totals, "top_sessions": ranked, "days": days}
