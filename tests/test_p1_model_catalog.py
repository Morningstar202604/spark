"""P1 模型目录（models.dev）离线单元测试。"""
from __future__ import annotations

from pathlib import Path

from spark import model_catalog

_FAKE = {
    "prov-a": {
        "models": {
            "m-cheap": {"name": "Cheap", "cost": {"input": 0.1, "output": 0.2}, "limit": {"context": 32000}},
            "m-big": {"name": "Big", "cost": {"input": 1.5, "output": 6.0}, "limit": {"context": 1000000}},
        }
    },
    "prov-b": {"models": {"other": {"name": "Other"}}},
}


def test_lookup_exact_and_miss():
    assert model_catalog.lookup(_FAKE, "prov-a", "m-cheap")["name"] == "Cheap"
    assert model_catalog.lookup(_FAKE, "prov-a", "nope") is None
    assert model_catalog.lookup(_FAKE, "prov-x", "m-cheap") is None


def test_search_filters_and_limits():
    rows = model_catalog.search(_FAKE, "m-cheap")
    assert [r["model"] for r in rows] == ["m-cheap"]
    rows_all = model_catalog.search(_FAKE, "", limit=100)
    assert len(rows_all) == 3  # 快照全集
    assert model_catalog.search(_FAKE, "zzz") == []


def test_estimate_cost_math():
    # 1M input tokens * 0.1 + 2M output tokens * 0.2 = 0.5
    assert model_catalog.estimate_cost(_FAKE, "prov-a", "m-cheap", 1_000_000, 2_000_000) == 0.5
    # 无价格数据 → None
    assert model_catalog.estimate_cost(_FAKE, "prov-b", "other", 100, 100) is None
    assert model_catalog.estimate_cost(_FAKE, "prov-a", "missing", 1, 1) is None


def test_fallback_snapshot_shape():
    assert "deepseek" in model_catalog._FALLBACK_SNAPSHOT
    assert model_catalog.lookup(model_catalog._FALLBACK_SNAPSHOT, "deepseek", "deepseek-chat")


def test_fetch_offline_uses_cache(tmp_path: Path, monkeypatch):
    # 写入"缓存"后断网（httpx.get 抛错）→ 应命中过期缓存
    cache = tmp_path / "cache" / "models-dev.json"
    cache.parent.mkdir(parents=True)
    import json

    cache.write_text(json.dumps(_FAKE))

    def boom(*a, **kw):
        raise OSError("offline")

    monkeypatch.setattr(model_catalog.httpx, "get", boom)
    data = model_catalog.fetch_catalog(tmp_path)
    assert model_catalog.lookup(data, "prov-a", "m-cheap")["name"] == "Cheap"


def test_fetch_offline_no_cache_falls_back_to_snapshot(tmp_path: Path, monkeypatch):
    def boom(*a, **kw):
        raise OSError("offline")

    monkeypatch.setattr(model_catalog.httpx, "get", boom)
    data = model_catalog.fetch_catalog(tmp_path)
    assert model_catalog.lookup(data, "deepseek", "deepseek-chat") is not None
