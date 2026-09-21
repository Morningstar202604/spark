from pathlib import Path

import pytest

from spark.errors import PathEscapeError
from spark.sandbox import WorkdirSandbox


def test_resolve_inside(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    target = box.resolve("src/app.py")
    assert target == (tmp_path / "src" / "app.py").resolve()


def test_reject_escape(tmp_path: Path) -> None:
    box = WorkdirSandbox(tmp_path)
    with pytest.raises(PathEscapeError):
        box.resolve("../secret")
