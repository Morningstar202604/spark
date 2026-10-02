"""设置抽屉 UI 结构契约（React 迁移版）。

R1: SettingsDrawer 必须保留六个设置分区，且初始停在模型页。
R2: 设置表单必须覆盖历史保存契约涉及的全部配置域，避免迁移后丢失字段。
R3: 保存按钮必须位于抽屉 footer，任何分区下都可见可点。
"""

from __future__ import annotations

from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
DRAWER = BASE / "spark2" / "web" / "src" / "components" / "SettingsDrawer.tsx"
STATE = BASE / "spark2" / "web" / "src" / "state.tsx"

DRAWER_HTML = DRAWER.read_text(encoding="utf-8")
STATE_HTML = STATE.read_text(encoding="utf-8")

PANES = [
    "paneModel",
    "paneWorkspace",
    "paneMemory",
    "paneIntegrations",
    "paneAdvanced",
    "paneAbout",
]

FORM_FIELD_TOKENS = [
    "provider",
    "base_url",
    "proxy",
    "model",
    "model_fast",
    "fallback_model",
    "api_key",
    "workdir",
    "approval_mode",
    "max_context_tokens",
    "memory_embedding",
    "memory_embed_model",
    "max_turns",
    "tool_timeout",
    "system_prompt",
    "temperature",
    "max_tokens",
    "route_enabled",
    "route_keywords",
    "usage_pricing",
    "protected_paths",
    "mcpServers",
    "token",
]

DOMAIN_COVERAGE = [
    "provider",
    "base_url",
    "model",
    "model_fast",
    "api_key",
    "workdir",
    "approval_mode",
    "max_context_tokens",
    "memory_embedding",
    "memory_embed_model",
    "token",
    "system_prompt",
    "protected_paths",
    "max_turns",
    "tool_timeout",
    "temperature",
    "max_tokens",
    "route_enabled",
    "route_keywords",
    "usage_pricing",
]


def _settings_panes_block() -> str:
    start = DRAWER_HTML.index("const SETTINGS_PANES")
    end = DRAWER_HTML.index("export function SettingsDrawer")
    return DRAWER_HTML[start:end]


def test_r1_panes_present_and_default_model() -> None:
    block = _settings_panes_block()
    for pane in PANES:
        assert f'"{pane}"' in block, f"缺少分区 {pane}"
    assert "paneModel" in DRAWER_HTML
    assert "useState(initialPane || \"paneModel\")" in DRAWER_HTML


def test_r2_field_coverage() -> None:
    text = f"{DRAWER_HTML}\n{STATE_HTML}"
    for token in FORM_FIELD_TOKENS:
        assert token in text, f"缺少字段 {token}"
    for domain in DOMAIN_COVERAGE:
        assert domain in DRAWER_HTML or domain in STATE_HTML, (
            f"缺少配置域 {domain}"
        )


def test_r3_footer_has_save() -> None:
    start = DRAWER_HTML.index("/* Footer */")
    end = DRAWER_HTML.index("// ---------- Sub-panes ----------")
    footer = DRAWER_HTML[start:end]
    assert "保存设置" in footer
    assert "handleSave" in footer
