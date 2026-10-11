"""Checkpoint capture/restore must never destroy a file it did not archive.

The restore pass deletes workdir files that are absent from the snapshot, which
is the right way to drop files created after the checkpoint — but a file the
snapshot *failed to read* (locked by another process on Windows, unreadable
permissions) is also absent from the archive, and used to be deleted anyway.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from spark.core import checkpoints as cp


@pytest.fixture
def isolated_snapshots(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "spark-home"
    monkeypatch.setenv("SPARK_HOME", str(home))
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "notes.md").write_text("original\n", encoding="utf-8")
    return workdir


def test_snapshot_records_files_it_could_not_archive(isolated_snapshots, monkeypatch) -> None:
    workdir = isolated_snapshots
    (workdir / "locked.csv").write_text("epoch,loss\n1,0.4\n", encoding="utf-8")

    real_add = tarfile.TarFile.add

    def failing_add(self, name, arcname=None, *args, **kwargs):
        if arcname == "locked.csv":
            raise OSError("another process is using the file")
        return real_add(self, name, arcname=arcname, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", failing_add)
    snapshot_id = cp.snapshot_workdir(workdir)

    manifest = cp._load_manifest(snapshot_id)
    assert manifest["unarchived"] == ["locked.csv"]
    assert "locked.csv" not in manifest["files"]


def test_restore_keeps_unarchived_file_and_still_prunes_new_ones(
    isolated_snapshots, monkeypatch
) -> None:
    workdir = isolated_snapshots
    critical = workdir / "locked.csv"
    critical.write_text("epoch,loss\n1,0.4\n", encoding="utf-8")

    real_add = tarfile.TarFile.add

    def failing_add(self, name, arcname=None, *args, **kwargs):
        if arcname == "locked.csv":
            raise OSError("another process is using the file")
        return real_add(self, name, arcname=arcname, *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", failing_add)
    snapshot_id = cp.snapshot_workdir(workdir)
    monkeypatch.setattr(tarfile.TarFile, "add", real_add)

    (workdir / "notes.md").write_text("agent rewrote this\n", encoding="utf-8")
    created_after = workdir / "scratch.txt"
    created_after.write_text("new since checkpoint\n", encoding="utf-8")

    result = cp.restore_workdir(workdir, snapshot_id)

    assert critical.exists(), "a file the snapshot never captured must not be deleted"
    assert critical.read_text(encoding="utf-8") == "epoch,loss\n1,0.4\n"
    assert (workdir / "notes.md").read_text(encoding="utf-8") == "original\n"
    assert not created_after.exists(), "files created after the checkpoint must be pruned"
    assert result["removed_new_files"] == 1


def test_old_manifests_without_the_unarchived_key_still_load(
    isolated_snapshots, monkeypatch
) -> None:
    """Manifests written before this fix have no ``unarchived`` key."""
    workdir = isolated_snapshots
    snapshot_id = cp.snapshot_workdir(workdir)
    path = cp._manifest_path(snapshot_id)
    data = cp._load_manifest(snapshot_id)
    data.pop("unarchived")
    cp._write_manifest(snapshot_id, data)

    assert cp._load_manifest(snapshot_id)["unarchived"] == []
    assert cp.restore_workdir(workdir, snapshot_id)["restored_files"] == 1
    assert path.is_file()
