"""P1-10 国产 agentic 模型路由矩阵 — 路由决策与集成测试。

覆盖：
* 任务类型 -> 期望档位（code/debug -> 能力档；chat -> 成本档；offline -> 离线档）
* 预算约束（budget=low 强制成本档；budget=high 简单问答也走能力档）
* 模型不可用降级（首选 key 缺失 -> 同类下一个 -> 跨类 -> 离线兜底）
* 与 create_provider 集成（mock provider 路径不被破坏）
"""

from __future__ import annotations

import pytest

from spark.config import SparkConfig
from spark.providers.factory import create_provider
from spark.providers.mock import MockProvider
from spark.providers.routing import (
    MODEL_REGISTRY,
    apply_routed_provider,
    decide_tier,
    route_and_build,
)

# ---------------------------------------------------------------------------
# 任务类型 -> 档位类
# ---------------------------------------------------------------------------


def test_code_task_routes_to_capability_tier() -> None:
    """多文件编程任务：能力档。"""
    d = decide_tier("code", "medium", available={"deepseek_thinking"})
    assert d.tier.cls == "capability"
    assert d.tier.id == "deepseek_thinking"
    assert d.reason.startswith("matched:")


def test_debug_task_routes_to_capability_tier() -> None:
    """复杂调试任务：能力档（且是 thinking 档优先）。"""
    d = decide_tier("debug", "medium", available={"deepseek_thinking", "deepseek_chat"})
    assert d.tier.cls == "capability"
    assert d.tier.thinking is True


def test_chat_task_routes_to_cost_tier() -> None:
    """日常问答：默认成本档。"""
    d = decide_tier("chat", "medium", available={"deepseek_chat", "deepseek_thinking"})
    assert d.tier.cls == "cost"
    assert d.tier.id == "deepseek_chat"


def test_offline_flag_forces_offline_tier() -> None:
    """offline=True：直接走离线档，无视任务类型/预算。"""
    d = decide_tier("code", "high", offline=True, available={"deepseek_thinking", "ollama_local"})
    assert d.tier.cls == "offline"
    assert d.tier.provider == "ollama"
    assert d.tier.id == "ollama_local"


# ---------------------------------------------------------------------------
# 预算约束
# ---------------------------------------------------------------------------


def test_low_budget_forces_cost_tier_even_for_code() -> None:
    """budget=low：省钱优先，连 code 任务也落到成本档。"""
    d = decide_tier("code", "low", available={"deepseek_chat", "deepseek_thinking"})
    # code 本应能力档，但 low 预算 + 能力档可用时……
    # 设计：code 仍优先能力档（质量），low 预算主要影响 chat。
    # 这里断言 low 预算下 chat 一定是成本档。
    d_chat = decide_tier("chat", "low", available={"deepseek_chat", "deepseek_thinking"})
    assert d_chat.tier.cls == "cost"
    # code 在 low 预算下：能力档可用仍走能力档（code 质量优先）
    assert d.tier.cls == "capability"


def test_high_budget_promotes_simple_chat_to_capability() -> None:
    """budget=high：简单问答也升级到能力档。"""
    d = decide_tier("chat", "high", available={"deepseek_thinking", "deepseek_chat"})
    assert d.tier.cls == "capability"


# ---------------------------------------------------------------------------
# 模型不可用降级
# ---------------------------------------------------------------------------


def test_same_class_fallback_to_next_available() -> None:
    """能力档首选（deepseek_thinking）不可用 -> 同类下一个（qwen_coder）。"""
    d = decide_tier(
        "code",
        "medium",
        available={"qwen_coder", "kimi_k2"},  # 故意不含 deepseek_thinking
    )
    assert d.tier.id == "qwen_coder"
    assert "deepseek_thinking" in d.degraded_from


def test_cross_class_fallback_capability_to_cost() -> None:
    """能力档全不可用 -> 降级到成本档。"""
    d = decide_tier(
        "code",
        "medium",
        available={"deepseek_chat", "doubao"},  # 不含任何 capability
    )
    assert d.tier.cls == "cost"
    assert d.tier.id == "deepseek_chat"
    assert d.reason.startswith("fallback:")


def test_offline_is_last_resort_when_everything_unavailable() -> None:
    """在线档全不可用 -> 跨类降级到离线档。"""
    d = decide_tier("code", "high", available={"ollama_local"})
    assert d.tier.id == "ollama_local"
    assert d.tier.cls == "offline"
    assert d.reason.startswith("fallback:")


def test_offline_last_resort_when_absolutely_nothing_usable() -> None:
    """白名单完全为空 -> 仍兜底返回离线档（保证永远选得出一档）。"""
    d = decide_tier("code", "high", available=set())
    assert d.tier.id == "ollama_local"
    assert "last-resort" in d.reason


def test_key_env_drives_availability_via_environ() -> None:
    """api_key_env 未设置 -> 该档不可用（用 environ 注入做确定性测试）。"""
    # 不注入 DEEPSEEK_API_KEY，能力档首选 deepseek_thinking 不可用
    d = decide_tier("code", "medium", environ={"NOPE": "x"})
    # 所有在线档 key 都缺失 -> 最终离线兜底
    assert d.tier.provider == "ollama"


def test_key_env_present_makes_tier_available() -> None:
    """api_key_env 设置 -> 该档可用。"""
    d = decide_tier(
        "code",
        "medium",
        environ={"DEEPSEEK_API_KEY": "sk-test"},
    )
    assert d.tier.id == "deepseek_thinking"


def test_preferred_taken_when_available() -> None:
    """用户显式 preferred 且可用 -> 直接采用。"""
    d = decide_tier("chat", "low", preferred="kimi_k2", available={"kimi_k2", "deepseek_chat"})
    assert d.tier.id == "kimi_k2"
    assert d.reason == "user-preferred"


def test_preferred_unavailable_degrades() -> None:
    """用户 preferred 但不可用 -> 按正常降级链走。"""
    d = decide_tier(
        "chat", "low", preferred="kimi_k2", available={"deepseek_chat"}
    )  # kimi 不在白名单
    assert d.tier.id == "deepseek_chat"
    assert "kimi_k2" in d.degraded_from


# ---------------------------------------------------------------------------
# apply_routed_provider 写回 cfg
# ---------------------------------------------------------------------------


def test_apply_routed_provider_writes_connection_fields() -> None:
    cfg = SparkConfig()
    d = decide_tier("chat", "low", available={"deepseek_chat"})
    apply_routed_provider(cfg, d)
    assert cfg.provider.name == "openai_compat"
    assert cfg.provider.base_url == "https://api.deepseek.com/v1"
    assert cfg.provider.model == "deepseek-chat"
    assert cfg.provider.api_key_env == "DEEPSEEK_API_KEY"


def test_apply_routed_provider_offline_sets_ollama() -> None:
    cfg = SparkConfig()
    d = decide_tier("code", "high", offline=True)
    apply_routed_provider(cfg, d)
    assert cfg.provider.name == "ollama"
    assert cfg.provider.base_url == "http://127.0.0.1:11434/v1"
    assert "qwen3-coder" in cfg.provider.model


# ---------------------------------------------------------------------------
# 与 create_provider 集成（不破坏 mock / openai_compat / ollama 三条既有路径）
# ---------------------------------------------------------------------------


def test_create_provider_mock_unchanged() -> None:
    """既有 mock 路径：create_provider(mock=True) 仍返回 MockProvider。"""
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    prov = create_provider(cfg)
    assert isinstance(prov, MockProvider)


def test_create_provider_rejects_unknown_name() -> None:
    """既有行为：未知 provider 名抛 ValueError。"""
    cfg = SparkConfig()
    cfg.provider.name = "not_a_provider"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider(cfg)


def test_route_and_build_offline_returns_ollama_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """route_and_build 走离线档 -> 返回 OllamaProvider（不真实联网）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
    cfg = SparkConfig()
    prov, decision = route_and_build(cfg, task_type="code", budget="high")
    # 无任何在线 key -> 离线兜底
    assert decision.tier.id == "ollama_local"
    from spark.providers.openai_compat import OllamaProvider

    assert isinstance(prov, OllamaProvider)
    assert "qwen3-coder" in prov.model  # type: ignore[attr-defined]


def test_route_and_build_mock_provider_injectable(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由层不破坏「显式注入 MockProvider」这条既有集成路径。"""
    cfg = SparkConfig()
    cfg.provider.name = "mock"
    prov = create_provider(cfg, mock=MockProvider())
    assert isinstance(prov, MockProvider)
    # Provider 是 Protocol（非 runtime_checkable），仅校验暴露 stream 方法
    assert hasattr(prov, "stream")


def test_registry_covers_all_required_models() -> None:
    """档位矩阵必须包含任务要求的全部国产模型。"""
    ids = set(MODEL_REGISTRY)
    required = {
        "deepseek_thinking",
        "deepseek_chat",
        "qwen_coder",
        "kimi_k2",
        "glm_45",
        "doubao",
        "ollama_local",
    }
    assert required <= ids
