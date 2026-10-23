"""知识库：分块、检索、持久化、路径安全。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark.knowledge import (
    CHUNK_CHARS,
    KnowledgeBase,
    _read_doc,
    _split_chunks,
    build_context_block,
    knowledge_root,
    project_key,
)


class TestSplitChunks:
    def test_empty_returns_empty(self) -> None:
        assert _split_chunks("") == []
        assert _split_chunks("   \n\n  ") == []

    def test_short_text_single_chunk(self) -> None:
        assert _split_chunks("hello world") == ["hello world"]

    def test_paragraphs_merge_under_limit(self) -> None:
        text = "a" * 100 + "\n\n" + "b" * 100
        chunks = _split_chunks(text, size=500)
        assert len(chunks) == 1
        assert "a" * 100 in chunks[0] and "b" * 100 in chunks[0]

    def test_long_paragraph_slides_with_overlap(self) -> None:
        text = "x" * (CHUNK_CHARS * 3)
        chunks = _split_chunks(text)
        assert len(chunks) >= 3
        # 滑窗重叠：相邻块共享一段内容
        assert chunks[0][-10:] in chunks[1]

    def test_crlf_normalised(self) -> None:
        assert "\r" not in _split_chunks("a\r\nb\r\n\r\nc")[0]


class TestReadDoc:
    def test_reads_utf8(self, tmp_path: Path) -> None:
        f = tmp_path / "a.md"
        f.write_text("你好", encoding="utf-8")
        assert _read_doc(f) == "你好"

    def test_unsupported_suffix(self, tmp_path: Path) -> None:
        f = tmp_path / "a.exe"
        f.write_bytes(b"\x00\x01")
        assert _read_doc(f) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _read_doc(tmp_path / "nope.md") is None

    def test_gbk_fallback(self, tmp_path: Path) -> None:
        f = tmp_path / "gbk.txt"
        f.write_bytes("中文".encode("gbk"))
        assert _read_doc(f) == "中文"


class TestKnowledgeBase:
    def test_add_and_list(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        doc = tmp_path / "guide.md"
        doc.write_text("# 指南\n\n这是鉴权流程的说明。", encoding="utf-8")
        meta = kb.add_file(doc)
        assert meta.name == "guide.md"
        assert meta.chunks >= 1
        assert [d.name for d in kb.docs()] == ["guide.md"]

    def test_readding_same_name_replaces(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        doc = tmp_path / "guide.md"
        doc.write_text("v1", encoding="utf-8")
        kb.add_file(doc)
        doc.write_text("v2 with more text", encoding="utf-8")
        kb.add_file(doc)
        assert len(kb.docs()) == 1

    def test_remove(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        doc = tmp_path / "a.md"
        doc.write_text("alpha beta", encoding="utf-8")
        meta = kb.add_file(doc)
        assert kb.remove(meta.id) is True
        assert kb.docs() == []
        assert kb.search("alpha") == []

    def test_remove_unknown_returns_false(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        assert kb.remove("nope") is False

    def test_search_ranks_relevant_first(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        a = tmp_path / "auth.md"
        a.write_text("鉴权走三层权限作用域，deny 优先。", encoding="utf-8")
        b = tmp_path / "style.md"
        b.write_text("缩进使用两个空格。", encoding="utf-8")
        kb.add_file(a)
        kb.add_file(b)
        hits = kb.search("鉴权 权限")
        assert hits, "expected at least one hit"
        assert hits[0]["doc_name"] == "auth.md"

    def test_search_empty_query(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        doc = tmp_path / "a.md"
        doc.write_text("something", encoding="utf-8")
        kb.add_file(doc)
        assert kb.search("") == []
        assert kb.search("   ") == []

    def test_search_no_docs(self, tmp_path: Path) -> None:
        assert KnowledgeBase(tmp_path / "kb").search("anything") == []

    def test_exact_phrase_bonus(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        doc = tmp_path / "a.md"
        doc.write_text("鉴权 流程 说明\n\n\n鉴权流程说明", encoding="utf-8")
        kb.add_file(doc)
        hits = kb.search("鉴权流程说明")
        assert hits and hits[0]["score"] > 0

    def test_empty_file_rejected(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        doc = tmp_path / "empty.md"
        doc.write_text("   ", encoding="utf-8")
        with pytest.raises(ValueError):
            kb.add_file(doc)

    def test_corrupt_index_recovers(self, tmp_path: Path) -> None:
        root = tmp_path / "kb"
        root.mkdir()
        (root / "index.json").write_text("{not json", encoding="utf-8")
        kb = KnowledgeBase(root)
        assert kb.docs() == []
        doc = tmp_path / "a.md"
        doc.write_text("hello", encoding="utf-8")
        kb.add_file(doc)
        assert len(kb.docs()) == 1

    def test_index_is_valid_json(self, tmp_path: Path) -> None:
        kb = KnowledgeBase(tmp_path / "kb")
        doc = tmp_path / "a.md"
        doc.write_text("content here", encoding="utf-8")
        kb.add_file(doc)
        data = json.loads((tmp_path / "kb" / "index.json").read_text(encoding="utf-8"))
        assert data["docs"] and data["chunks"]


class TestProjectKey:
    def test_stable_for_same_path(self, tmp_path: Path) -> None:
        assert project_key(tmp_path) == project_key(tmp_path)

    def test_different_paths_differ(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert project_key(a) != project_key(b)

    def test_knowledge_root_under_home(self, tmp_path: Path) -> None:
        root = knowledge_root(tmp_path, tmp_path)
        assert root.parent.parent == tmp_path


class TestBuildContextBlock:
    def test_empty_returns_empty(self) -> None:
        assert build_context_block([]) == ""

    def test_has_untrusted_fence(self) -> None:
        block = build_context_block([{"doc_name": "a.md", "index": 0, "text": "hello", "score": 1.0}])
        assert block.startswith("<knowledge_base>")
        assert block.rstrip().endswith("</knowledge_base>")
        assert "不要执行" in block

    def test_respects_max_chars(self) -> None:
        hits = [{"doc_name": "a.md", "index": i, "text": "x" * 500, "score": 1.0} for i in range(20)]
        block = build_context_block(hits, max_chars=1000)
        assert len(block) < 2000
