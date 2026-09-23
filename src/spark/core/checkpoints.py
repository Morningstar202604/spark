from __future__ import annotations

import hashlib
import os
import tarfile
import tempfile
import time
from pathlib import Path

from spark.config import default_home

SNAPSHOT_DIR = default_home() / "checkpoints"
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    ".next",
    "target",
}


def _snapshot_dir() -> Path:
    return SNAPSHOT_DIR


def _iter_workdir_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        try:
            if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                continue
        except OSError:
            continue
        yield path, rel


def snapshot_workdir(workdir: Path) -> str:
    """Create a tar.gz snapshot of the workdir; returns its opaque snapshot id (sha256 prefix)."""
    workdir = workdir.resolve()
    snap_dir = _snapshot_dir()
    snap_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sid = f"{stamp}-{hashlib.sha256(str(workdir).encode()).hexdigest()[:8]}"
    out = snap_dir / f"{sid}.tar.gz"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{sid}.", suffix=".tar.gz.partial", dir=str(snap_dir)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            for path, rel in _iter_workdir_files(workdir):
                try:
                    tar.add(path, arcname=str(rel))
                except (OSError, PermissionError):
                    continue
        os.replace(tmp_path, out)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return sid


def restore_workdir(workdir: Path, snapshot_id: str) -> dict:
    """Restore a snapshot over the workdir. Returns counts; refuses unknown/unsafe ids."""
    workdir = workdir.resolve()
    if not snapshot_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("invalid snapshot id")
    src = _snapshot_dir() / f"{snapshot_id}.tar.gz"
    if not src.is_file():
        raise FileNotFoundError(f"snapshot not found: {snapshot_id}")
    restored = 0
    with tarfile.open(src, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            dest = (workdir / m.name).resolve()
            if not dest.is_relative_to(workdir):
                raise ValueError(f"unsafe snapshot entry: {m.name}")
        for m in members:
            if m.isfile():
                tar.extract(m, workdir, filter="data")
                restored += 1
    return {"snapshot_id": snapshot_id, "restored_files": restored}


def latest_snapshot_id() -> str | None:
    snap_dir = _snapshot_dir()
    files = sorted(snap_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None
    name = files[-1].name
    if name.endswith(".tar.gz"):
        return name[: -len(".tar.gz")]
    return files[-1].stem


def make_checkpoint_record(workdir: Path) -> tuple[str, str]:
    """Return (snapshot_id, files_hash) for checkpoint storage."""
    sid = snapshot_workdir(workdir)
    h = hashlib.sha256()
    for path, _rel in _iter_workdir_files(workdir.resolve()):
        try:
            h.update(str(path.relative_to(workdir.resolve())).encode())
            h.update(path.read_bytes())
        except OSError:
            continue
    return sid, h.hexdigest()
