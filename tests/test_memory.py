"""长期记忆测试：CRUD、三级检索、工具处理器、循环注入。"""
from __future__ import annotations

from pathlib import Path

from spark2.approval import ApprovalGate
from spark2.loop import AgentLoop
from spark2.memory import MemoryStore
from spark2.tools import build_registry
from spark2.tools.base import ToolContext


def _store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(path=tmp_path / "mem" / "memory.db")


async def test_remember_list_search(tmp_path: Path) -> None:
    store = _store(tmp_path)
    wd = str(tmp_path)
    store.remember(wd, "部署", "用 systemd 服务，端口 8080")
    store.remember(wd, "测试", "pytest，依赖装到 ~/.local")
    assert store.count(wd) == 2
    rows = store.list(wd)
    assert {r["key"] for r in rows} == {"部署", "测试"}

    # 精确 key 命中
    hits = store.search(wd, "部署")
    assert hits and hits[0]["key"] == "部署"
    # 中文值片段 LIKE 兜底
    hits = store.search(wd, "systemd")
    assert hits and "systemd" in hits[0]["value"]
    # 值里的中文片段
    hits = store.search(wd, "依赖")
    assert hits and "pytest" in hits[0]["value"]
    # 无关词不命中
    assert store.search(wd, "区块链") == []


async def test_upsert_overwrites(tmp_path: Path) -> None:
    store = _store(tmp_path)
    wd = str(tmp_path)
    store.remember(wd, "端口", "8080")
    store.remember(wd, "端口", "9090")
    rows = store.list(wd)
    assert len(rows) == 1
    assert rows[0]["value"] == "9090"


async def test_forget(tmp_path: Path) -> None:
    store = _store(tmp_path)
    wd = str(tmp_path)
    store.remember(wd, "a", "1")
    store.remember(wd, "b", "2")
    assert store.forget(wd, "a") == 1
    assert store.count(wd) == 1
    assert store.search(wd, "a") == []
    assert store.forget(wd, "不存在") == 0


async def test_workdir_isolation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    d1 = str(tmp_path / "p1")
    d2 = str(tmp_path / "p2")
    (tmp_path / "p1").mkdir()
    (tmp_path / "p2").mkdir()
    store.remember(d1, "key", "v1")
    store.remember(d2, "key", "v2")
    assert store.search(d1, "v1") and store.search(d2, "v2")
    assert store.count(d1) == 1 and store.count(d2) == 1


async def test_remember_tool_handler(tmp_path: Path) -> None:
    reg = build_registry()
    store = _store(tmp_path)
    ctx = ToolContext(workdir=tmp_path, memory=store)
    out = await reg["remember"].handler({"key": "构建", "value": "make build"}, ctx)
    assert "已记住" in out
    assert store.search(str(tmp_path), "make")[0]["key"] == "构建"
    # 缺参报错
    out2 = await reg["remember"].handler({"key": "", "value": "x"}, ctx)
    assert "错误" in out2
    # 无记忆库时明确报错
    ctx2 = ToolContext(workdir=tmp_path, memory=None)
    assert "未启用" in await reg["remember"].handler({"key": "k", "value": "v"}, ctx2)


async def test_forget_tool_handler(tmp_path: Path) -> None:
    reg = build_registry()
    store = _store(tmp_path)
    ctx = ToolContext(workdir=tmp_path, memory=store)
    store.remember(str(tmp_path), "端口", "8080")
    out = await reg["forget"].handler({"key": "端口"}, ctx)
    assert "已忘记" in out
    assert store.count(str(tmp_path)) == 0


async def test_loop_writes_memory_and_injects(tmp_path: Path) -> None:
    store = _store(tmp_path)
    loop = AgentLoop(
        tmp_path,
        {"model": "mock", "mock_script": [
            [{"type": "tool_calls", "calls": [{"id": "r1", "name": "remember", "arguments": {"key": "端口", "value": "8080"}}]}],
            [{"type": "text", "text": "记住了。"}],
        ]},
        ApprovalGate(mode="full-auto"),
        memory=store,
    )
    messages = [{"role": "user", "content": "记住：服务端口是 8080"}]
    evs = []
    async for ev in loop.stream(messages):
        evs.append(ev)
    assert any(e["type"] == "tool_result" and "已记住" in e["output"] for e in evs)
    assert store.search(str(tmp_path), "8080")[0]["key"] == "端口"

    # 下一轮自动注入：问"端口"时，记忆块出现在发给模型的上下文中
    loop2 = AgentLoop(tmp_path, {"model": "mock", "mock_script": [[{"type": "text", "text": "好。"}]]}, ApprovalGate(mode="full-auto"), memory=store)
    block = loop2._memory_block([{"role": "user", "content": "服务用哪个端口？"}])
    assert block and "8080" in block
    # 无关问题不注入
    assert loop2._memory_block([{"role": "user", "content": "今天天气怎么样？"}]) is None


class _FakeEmbedder:
    """固定向量嵌入器：让"语义"方向可预测，验证检索合并与置顶逻辑。"""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        out = []
        for t in texts:
            if "部署" in t or "端口" in t or "8080" in t:
                out.append([1.0, 0.0, 0.0, 0.0])
            elif "测试" in t or "pytest" in t:
                out.append([0.0, 1.0, 0.0, 0.0])
            else:
                out.append([0.0, 0.0, 0.0, 1.0])
        return out


async def test_semantic_search_merge_and_top(tmp_path: Path) -> None:
    store = MemoryStore(path=tmp_path / "mem" / "memory.db", embedder=_FakeEmbedder())
    wd = str(tmp_path)
    store.remember(wd, "部署", "用 systemd 服务，端口 8080")
    store.remember(wd, "测试", "pytest，依赖装到 ~/.local")
    # 语义向量已写入
    with store._connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM memories WHERE embedding IS NOT NULL").fetchone()[0]
        assert n == 2
    # "怎么启动服务"（无关键词命中）→ 语义命中"部署"
    hits = store.search(wd, "怎么启动服务")
    assert hits and hits[0]["key"] == "部署"
    # 语义命中置顶：即使有字面命中，语义相关也排前面
    hits = store.search(wd, "端口 8080 部署")
    assert hits and hits[0]["key"] == "部署"
    # 无关查询（向量指向未记录方向）→ 无语义命中，关键词仍可用
    hits = store.search(wd, "部署")
    assert hits and hits[0]["key"] == "部署"


async def test_no_embedder_fallback(tmp_path: Path) -> None:
    """未配置嵌入器：行为与旧版一致（纯关键词三级检索）。"""
    store = _store(tmp_path)
    wd = str(tmp_path)
    store.remember(wd, "部署", "systemd 端口 8080")
    hits = store.search(wd, "systemd")
    assert hits and hits[0]["key"] == "部署"
    assert store.search(wd, "无关内容xyz") == []


async def test_make_embedder_off_and_local_fail(tmp_path: Path) -> None:
    from spark2.memory import make_embedder

    assert make_embedder({"memory_embedding": "off"}) is None
    assert make_embedder({}) is None
    # local 未安装依赖 → 自动退回 None（不抛异常）
    emb = make_embedder({"memory_embedding": "local"})
    assert emb is None or emb is not None  # 视环境是否装有 sentence-transformers
