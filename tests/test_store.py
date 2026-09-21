from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from spark.models import ChatMessage
from spark.store import SessionStore


def test_resume_restores_order(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session(tmp_path, "mock", title="t")
    store.append_message(sid, ChatMessage(role="user", content="one"))
    store.append_message(sid, ChatMessage(role="assistant", content="two"))
    store.append_message(sid, ChatMessage(role="user", content="three"))
    loaded = store.load_messages(sid)
    assert [m.content for m in loaded] == ["one", "two", "three"]
    listed = store.list_sessions()
    assert listed[0]["id"] == sid
    store.close()


def test_store_is_usable_from_other_threads(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.db")
    sid = store.create_session(tmp_path, "mock", title="t")

    def write(i: int) -> None:
        store.append_message(sid, ChatMessage(role="user", content=f"m{i}"))

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(8)))
    loaded = store.load_messages(sid)
    assert len(loaded) == 8
    store.close()
