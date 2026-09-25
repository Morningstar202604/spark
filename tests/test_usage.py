"""用量统计测试（P3 ⑦）：记录、会话聚合、全局聚合、单价匹配。"""
from __future__ import annotations

from pathlib import Path

from spark2.usage import UsageStore, _match_pricing


def test_record_and_session_summary(tmp_path: Path) -> None:
    store = UsageStore(root=tmp_path / "usage")
    store.record("s1", "deepseek-v4-pro", 1000, 500)
    store.record("s1", "deepseek-v4-pro", 2000, 1000)
    store.record("s2", "doubao-1-5-pro-32k", 5000, 1000)
    s1 = store.session_summary("s1")
    assert s1["totals"]["prompt_tokens"] == 3000
    assert s1["totals"]["completion_tokens"] == 1500
    assert s1["totals"]["calls"] == 2
    # 费用 = 3000×4.5/1e6 + 1500×13.5/1e6（est_cost 显示为 4 位小数）
    assert abs(s1["totals"]["est_cost"] - (3000 * 4.5 + 1500 * 13.5) / 1e6) < 1e-3
    assert "deepseek-v4-pro" in s1["by_model"]


def test_global_summary_ranks(tmp_path: Path) -> None:
    store = UsageStore(root=tmp_path / "usage")
    store.record("s1", "deepseek-v4-flash", 1000, 1000)
    store.record("s2", "deepseek-v4-pro", 10000, 5000)
    g = store.global_summary()
    assert g["totals"]["calls"] == 2
    assert g["top_sessions"][0]["session_id"] == "s2"  # 花费更高排前


def test_pricing_override_and_prefix(tmp_path: Path) -> None:
    store = UsageStore(root=tmp_path / "usage", pricing={"my-model": {"input": 1.0, "output": 2.0}})
    assert _match_pricing("my-model", store.pricing) == {"input": 1.0, "output": 2.0}
    # 前缀匹配：deepseek-v4-pro 命中默认
    assert _match_pricing("deepseek-v4-pro", store.pricing)["input"] == 4.5
    # 未知模型走兜底
    assert _match_pricing("unknown-xyz", store.pricing)["input"] == 1.0
