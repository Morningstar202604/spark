"""Cost accounting: real usage → USD estimate via the models.dev catalog.

The catalog lookup and estimate existed but were never wired into the token
usage read path, so every status response reported tokens with no cost.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from spark.store import SessionStore


def _store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "s.db")


def test_cost_computed_from_catalog(tmp_path: Path) -> None:
    """Use a model that exists in the bundled snapshot so the test is deterministic."""
    from spark.model_catalog import _FALLBACK_SNAPSHOT, estimate_cost

    model = "deepseek-chat"
    expected = estimate_cost(_FALLBACK_SNAPSHOT, "deepseek", model, 1_000_000, 500_000)
    assert expected is not None, "fixture model missing from the bundled catalog"

    store = _store(tmp_path)
    sid = store.create_session(tmp_path, model)
    store.append_token_usage(sid, None, 1_000_000, 500_000, 0)
    usage = store.session_token_usage(sid, model=model)
    assert usage["cost_usd"] == pytest.approx(expected, rel=1e-3)
    assert usage["input_tokens"] == 1_000_000
    assert usage["calls"] == 1


def test_unknown_model_reports_none_not_zero(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(tmp_path, "private-model")
    store.append_token_usage(sid, None, 1000, 1000, 0)
    usage = store.session_token_usage(sid, model="totally-unknown-model-xyz")
    assert usage["cost_usd"] is None, "unknown pricing must not be reported as free"


def test_no_model_means_no_cost_field_value(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(tmp_path, "m")
    store.append_token_usage(sid, None, 10, 10, 0)
    assert store.session_token_usage(sid)["cost_usd"] is None


def test_cost_scales_with_tokens(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sid = store.create_session(tmp_path, "deepseek-chat")
    store.append_token_usage(sid, None, 100_000, 0, 0)
    small = store.session_token_usage(sid, model="deepseek-chat")["cost_usd"]
    store.append_token_usage(sid, None, 900_000, 0, 0)
    big = store.session_token_usage(sid, model="deepseek-chat")["cost_usd"]
    assert big == pytest.approx(small * 10, rel=1e-3)
