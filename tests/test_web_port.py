"""端口自适应：默认从 8000 起挑可用端口；显式端口被占由调用方报错。"""

from __future__ import annotations

import socket

import pytest

from spark.web.utils import _port_in_use, pick_free_port


def _listen() -> tuple[socket.socket, int]:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def test_pick_free_port_skips_occupied() -> None:
    holder, busy = _listen()
    try:
        picked = pick_free_port("127.0.0.1", busy, attempts=16)
        assert picked > busy
        assert not _port_in_use("127.0.0.1", picked)
    finally:
        holder.close()


def test_pick_free_port_raises_when_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("spark.web.utils._port_in_use", lambda host, port: True)
    with pytest.raises(OSError):
        pick_free_port("127.0.0.1", 8500, attempts=3)
