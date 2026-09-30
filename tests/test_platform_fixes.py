"""平台兼容与数据完整性回归（Windows 部署审查发现的问题锁定）。

覆盖：
1) apply_patch 必须保留文件尾换行（unified diff 语义），不得静默剥掉最后一个 \n；
2) apply_patch / write_file 必须保留目标文件既有行尾约定（CRLF 文件补丁后仍是 CRLF，
   LF 文件在 Windows 上不得被文本模式翻译成 CRLF——否则 git 显示全文件改动）；
3) search 降级路径（无 rg）必须按 UTF-8 解码（与 read_file 一致），中文检索可用；
4) provider 的 tiktoken 编码器必须惰性初始化：get_encoding 首次调用需联网下载 BPE，
   失败（无网/被墙）不得阻断 import 与整个应用启动，应回退启发式估算；
5) SPARK2_HOME 契约：sessions/logs/usage/codeindex/plugins/recent_dirs/保护路径
   必须在调用时动态取 config_dir()，不得固化为真实 ~/.spark2（config.py 注释自证）；
6) 会话 id / 终端 tab id 必须消毒，防 "../" 与绝对路径穿越（store.meta 曾可用
   "../outside" 读到会话目录之外的 meta.json）；
7) Windows 路径大小写不敏感：审批门的边界/保护判定不得因大小写差异被绕过。
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

from spark2.patch_apply import apply_patch
from spark2.store import SessionStore
from spark2.tools.base import ToolContext
from spark2.tools.fs import search, write_file

# ---------- 1/2) apply_patch 尾换行与行尾保留 ----------


def test_apply_patch_preserves_trailing_newline(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"x = 1\nprint(x)\n")
    patch = (
        "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n x = 1\n-print(x)\n+print(x + 1)\n"
    )
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "a.py").read_bytes() == b"x = 1\nprint(x + 1)\n"


def test_apply_patch_new_file_ends_with_newline(tmp_path: Path) -> None:
    patch = "--- a/n.md\n+++ b/n.md\n@@ -0,0 +1,2 @@\n+# t\n+body\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "n.md").read_bytes() == b"# t\nbody\n"


def test_apply_patch_keeps_missing_trailing_newline(tmp_path: Path) -> None:
    """原文件本来就没有尾换行 → 补丁后也不擅自添加。"""
    (tmp_path / "b.txt").write_bytes(b"a\nb")
    patch = "--- a/b.txt\n+++ b/b.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+b2\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "b.txt").read_bytes() == b"a\nb2"


def test_apply_patch_preserves_crlf(tmp_path: Path) -> None:
    (tmp_path / "c.txt").write_bytes(b"a\r\nb\r\n")
    patch = "--- a/c.txt\n+++ b/c.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+b2\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "c.txt").read_bytes() == b"a\r\nb2\r\n"


def test_apply_patch_lf_file_stays_lf(tmp_path: Path) -> None:
    """Windows 文本模式默认把 \n 翻译成 \r\n：LF 文件补丁后必须保持 LF。"""
    (tmp_path / "lf.txt").write_bytes(b"a\nb\n")
    patch = "--- a/lf.txt\n+++ b/lf.txt\n@@ -1,2 +1,2 @@\n a\n-b\n+b2\n"
    apply_patch(patch, tmp_path, [])
    assert (tmp_path / "lf.txt").read_bytes() == b"a\nb2\n"


async def test_write_file_lf_file_stays_lf(tmp_path: Path) -> None:
    p = tmp_path / "w.txt"
    p.write_bytes(b"old\n")
    ctx = ToolContext(workdir=tmp_path)
    await write_file({"path": "w.txt", "content": "new\nline\n"}, ctx)
    assert p.read_bytes() == b"new\nline\n"


async def test_write_file_preserves_crlf(tmp_path: Path) -> None:
    p = tmp_path / "crlf.txt"
    p.write_bytes(b"old\r\n")
    ctx = ToolContext(workdir=tmp_path)
    await write_file({"path": "crlf.txt", "content": "new\r\nline\n"}, ctx)
    assert p.read_bytes() == b"new\r\nline\r\n"


async def test_write_file_new_file_writes_content_verbatim(tmp_path: Path) -> None:
    ctx = ToolContext(workdir=tmp_path)
    await write_file({"path": "n.txt", "content": "a\nb\n"}, ctx)
    assert (tmp_path / "n.txt").read_bytes() == b"a\nb\n"


# ---------- 3) search 降级路径 UTF-8 ----------


async def test_search_fallback_matches_utf8_chinese(
    tmp_path: Path, monkeypatch
) -> None:
    """无 rg 时的 Python 遍历必须显式 UTF-8 解码（本机默认编码碰巧是 UTF-8，
    此测试锁定行为；cp936/cp1252 默认编码的机器上修复前中文检索必然失效）。"""
    monkeypatch.setattr("spark2.tools.fs.shutil.which", lambda _name: None)
    (tmp_path / "zh.txt").write_text(
        "部署说明：先安装依赖再启动服务\n", encoding="utf-8"
    )
    ctx = ToolContext(workdir=tmp_path)
    out = await search({"query": "部署说明", "path": "."}, ctx)
    assert "zh.txt" in out


# ---------- 4) tiktoken 惰性初始化 ----------


def test_provider_survives_tiktoken_init_failure(monkeypatch) -> None:
    """tiktoken.get_encoding 首次调用需联网下载 BPE；失败不得炸掉 import/启动。"""
    fake = types.ModuleType("tiktoken")

    def boom(name: str):
        raise RuntimeError("network unreachable")

    fake.get_encoding = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tiktoken", fake)
    import spark2.provider as prov

    importlib.reload(prov)
    try:
        assert prov.estimate_tokens("hello world 你好") > 0
    finally:
        importlib.reload(prov)  # 还原模块状态，避免影响其他测试


# ---------- 5) SPARK2_HOME 动态隔离 ----------


def test_codeindex_cache_respects_spark2_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "idx-home"
    monkeypatch.setenv("SPARK2_HOME", str(home))
    from spark2 import codeindex as ci

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    ci.index_project(str(proj), use_cache=False)
    assert (home / "codeindex").is_dir()


def test_session_store_default_root_respects_spark2_home(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "st-home"
    monkeypatch.setenv("SPARK2_HOME", str(home))
    s = SessionStore()
    assert s.root == home / "sessions"


def test_plugins_dir_respects_spark2_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "pl-home"
    monkeypatch.setenv("SPARK2_HOME", str(home))
    from spark2.plugins import plugins_dir

    assert plugins_dir() == home / "plugins"


def test_recent_dirs_respects_spark2_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "rd-home"
    monkeypatch.setenv("SPARK2_HOME", str(home))
    from spark2 import recent_dirs as rd

    rd.remember(str(tmp_path / "someproj"))
    assert (home / "recent_dirs.json").exists()
    assert rd.load_recent() == [str(tmp_path / "someproj")]


def test_loop_protected_paths_respect_spark2_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "lp-home"
    monkeypatch.setenv("SPARK2_HOME", str(home))
    from spark2.approval import ApprovalGate
    from spark2.loop import AgentLoop

    lp = AgentLoop(tmp_path, {"model": "mock"}, ApprovalGate())
    assert lp.ctx.protected[0] == home.resolve()


def test_appstate_dirs_respect_spark2_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "web-home"
    monkeypatch.setenv("SPARK2_HOME", str(home))
    from spark2.web.server import AppState

    cfg = {
        "provider": "mock",
        "base_url": "",
        "model": "mock",
        "api_key": "",
        "workdir": str(tmp_path),
        "approval_mode": "suggest",
        "max_context_tokens": 32000,
        "token": "t",
        "mock_script": None,
    }
    st = AppState(cfg=cfg)
    assert st.log_dir == home / "logs"
    assert Path(st.usage.root) == home / "usage"
    assert st.store.root == home / "sessions"


# ---------- 6) 路径穿越消毒 ----------


def test_session_store_rejects_traversal_sid(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    s = SessionStore(root=root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "meta.json").write_text('{"id":"evil"}', encoding="utf-8")
    assert s.meta("../outside") is None
    assert s.messages("../outside") == []
    assert s.delete("../outside") is False
    assert s.rename("../outside", "x") is False
    assert s.fork("../outside") is None
    # 绝对路径同样拒绝（Path(root) / "C:\\..." 会被绝对路径接管）
    assert s.meta(str(outside)) is None


def test_safe_tab_id_sanitized() -> None:
    from spark2.pty import safe_tab_id

    assert safe_tab_id("../../evil") == "evil"
    assert safe_tab_id("abc-123_X") == "abc-123_X"
    assert safe_tab_id("") != ""  # 空 → 自动生成，不会落到共享文件名
    assert "/" not in safe_tab_id("a/b\\c") and "\\" not in safe_tab_id("a/b\\c")


# ---------- 7) Windows 大小写不敏感边界 ----------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 路径大小写行为")
def test_approval_boundary_case_insensitive(tmp_path: Path) -> None:
    from spark2.approval import ApprovalGate
    from spark2.tools import build_registry

    reg = build_registry()
    wd_upper = Path(str(tmp_path).upper())
    d, _ = ApprovalGate(mode="full-auto").decide(
        reg["write_file"], {"path": str(tmp_path / "x.txt")}, wd_upper, []
    )
    assert d == "allow"  # 同一目录，仅大小写不同 → 仍在边界内
    d2, _ = ApprovalGate(mode="full-auto").decide(
        reg["write_file"], {"path": str(tmp_path.parent / "e.txt")}, tmp_path, []
    )
    assert d2 == "ask"  # 工作区外 → 大小写不影响必问


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 路径大小写行为")
def test_protected_path_denied_case_insensitive(tmp_path: Path) -> None:
    from spark2.approval import ApprovalGate
    from spark2.tools import build_registry

    reg = build_registry()
    (tmp_path / "secret").mkdir()
    prot = [Path(str(tmp_path / "secret").upper())]
    d, _ = ApprovalGate().decide(
        reg["write_file"], {"path": str(tmp_path / "secret" / "s.txt")}, tmp_path, prot
    )
    assert d == "deny"
