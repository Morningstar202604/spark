"""设置抽屉 UI 结构契约（Codex 式分类导航重写的行为锁）。

R1: 设置抽屉必须是「左侧分类导航 + 右侧分区面板」结构，六个分区齐全且初始停在模型页。
R2: saveCfg / loadConfig / bind 依赖的每个字段 id 在 index.html 中恰好出现一次
    （防分区搬迁时丢字段、防重复 id 导致 $() 取错节点）。
R3: 保存按钮与状态条必须位于抽屉 footer（任何分区下都可见可点）。
"""

from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).resolve().parents[1] / "spark2" / "web" / "index.html"
HTML = INDEX.read_text(encoding="utf-8")

PANES = [
    "paneModel",
    "paneWorkspace",
    "paneMemory",
    "paneIntegrations",
    "paneAdvanced",
    "paneAbout",
]

# saveCfg 提交 / loadConfig 回填 / bind 绑定的全部设置域字段 id
FIELD_IDS = [
    "fProvider",
    "fBaseUrl",
    "fModel",
    "fFastModel",
    "fKey",
    "fWorkdir",
    "fApproval",
    "fMaxCtx",
    "fMemoryEmbed",
    "fMemModel",
    "fMemModelRow",
    "fAuthToken",
    "btnClearToken",
    "fSysPrompt",
    "fProtPaths",
    "fMaxTurns",
    "fToolTimeout",
    "fTemp",
    "fMaxTokens",
    "fRouteOn",
    "fRouteKw",
    "fPricing",
    "fMemKey",
    "fMemVal",
    "btnAddMem",
    "mcpList",
    "btnAddMcp",
    "memoryList",
    "pluginList",
    "recentDirs",
    "dirsBox",
    "aboutVer",
    "btnTest",
    "btnSaveCfg",
    "cfgStatus",
    "fQuickAp",
]


def _settings_drawer() -> str:
    start = HTML.index('id="drawerSettings"')
    end = HTML.index("<!-- 用量统计抽屉 -->")
    return HTML[start:end]


def test_r1_nav_and_panes() -> None:
    d = _settings_drawer()
    for pane in PANES:
        assert f'id="{pane}"' in d, f"缺少分区 {pane}"
    navs = re.findall(r'<button[^>]*data-pane="(\w+)"', d)
    assert navs == PANES, f"导航项不符：{navs}"
    m = re.search(r'<button[^>]*data-pane="paneModel"[^>]*>', d)
    assert m and 'class="on"' in m.group(0), "模型页应为初始激活分区"
    m = re.search(r'<div[^>]*id="paneModel"[^>]*>', d)
    assert m and "setpane on" in m.group(0), "paneModel 应带 setpane on"


def test_r2_field_ids_unique() -> None:
    for fid in FIELD_IDS:
        n = HTML.count(f'id="{fid}"')
        assert n == 1, f'id="{fid}" 出现 {n} 次，应恰好 1 次'


def test_r3_footer_holds_save() -> None:
    d = _settings_drawer()
    foot = re.search(r'<div class="dfoot">.*?</div>\s*</div>', d, re.S)
    assert foot, "设置抽屉缺少 footer"
    assert 'id="btnSaveCfg"' in foot.group(0), "保存按钮应在 footer"
    assert 'id="cfgStatus"' in foot.group(0), "状态条应在 footer"
    assert 'id="btnTest"' not in foot.group(0), "测试连接属于模型分区，不应在 footer"
