"""Git-workdir snapshot checkpointer with incremental tar.gz archives."""
from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path, PurePosixPath, PureWindowsPath
from stat import S_ISREG

from spark.config import default_home

SNAPSHOT_DIR = default_home() / "checkpoints"
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024
MANIFEST_VERSION = 1
MANIFEST_SUFFIX = ".manifest.json"
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


def _root_key(root: Path) -> str:
    return os.path.normcase(str(root.resolve()))


def _valid_snapshot_id(snapshot_id: str) -> bool:
    return bool(snapshot_id) and snapshot_id.replace("-", "").replace("_", "").isalnum()


def _manifest_path(snapshot_id: str) -> Path:
    return _snapshot_dir() / f"{snapshot_id}{MANIFEST_SUFFIX}"


def _manifest_name_parts(name: str) -> tuple[str, ...]:
    if (
        not isinstance(name, str)
        or not name
        or "\x00" in name
        or "\\" in name
        or PurePosixPath(name).is_absolute()
        or bool(PureWindowsPath(name).drive)
    ):
        raise ValueError(f"unsafe snapshot entry: {name}")
    parts = tuple(name.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"unsafe snapshot entry: {name}")
    return parts


def _load_manifest(snapshot_id: str) -> dict | None:
    path = _manifest_path(snapshot_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid snapshot manifest: {snapshot_id}") from exc
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise ValueError(f"invalid snapshot manifest: {snapshot_id}")
    if not isinstance(data.get("root"), str) or not isinstance(data.get("files"), dict):
        raise ValueError(f"invalid snapshot manifest: {snapshot_id}")
    baseline = data.get("baseline_snapshot_id")
    if baseline is not None and (
        not isinstance(baseline, str)
        or not _valid_snapshot_id(baseline)
        or baseline == snapshot_id
    ):
        raise ValueError(f"invalid snapshot manifest: {snapshot_id}")
    for name, metadata in data["files"].items():
        _manifest_name_parts(name)
        if (
            not isinstance(metadata, list)
            or len(metadata) != 2
            or any(type(value) is not int for value in metadata)
        ):
            raise ValueError(f"invalid snapshot manifest: {snapshot_id}")
    return data


def _write_manifest(snapshot_id: str, data: dict) -> None:
    snap_dir = _snapshot_dir()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{snapshot_id}.", suffix=".manifest.partial", dir=str(snap_dir)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                data, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        os.replace(tmp_path, _manifest_path(snapshot_id))
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _iter_workdir_files(root: Path, excluded_root: Path | None = None):
    excluded = excluded_root.resolve() if excluded_root is not None else None
    if excluded is not None and root == excluded:
        return
    for directory, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(directory)
        kept_dirs: list[str] = []
        for name in dirnames:
            if name in SKIP_DIRS:
                continue
            candidate = current / name
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if excluded is not None and resolved == excluded:
                continue
            kept_dirs.append(name)
        dirnames[:] = sorted(kept_dirs)
        for name in sorted(filenames):
            path = current / name
            try:
                stat_result = path.lstat()
            except OSError:
                continue
            if (
                not S_ISREG(stat_result.st_mode)
                or stat_result.st_size > MAX_SNAPSHOT_BYTES
            ):
                continue
            rel = path.relative_to(root)
            yield path, rel, [stat_result.st_size, stat_result.st_mtime_ns]


def _manifest_mtime_ns(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _find_baseline(workdir: Path) -> str | None:
    root_key = _root_key(workdir)
    manifests = sorted(
        _snapshot_dir().glob(f"*{MANIFEST_SUFFIX}"),
        key=_manifest_mtime_ns,
        reverse=True,
    )
    for manifest_path in manifests:
        snapshot_id = manifest_path.name[: -len(MANIFEST_SUFFIX)]
        if not _valid_snapshot_id(snapshot_id):
            continue
        try:
            manifest = _load_manifest(snapshot_id)
        except ValueError:
            continue
        if (
            manifest is not None
            and manifest.get("root") == root_key
            and manifest.get("baseline_snapshot_id") is None
            and (_snapshot_dir() / f"{snapshot_id}.tar.gz").is_file()
        ):
            return snapshot_id
    return None


def snapshot_workdir(workdir: Path) -> str:
    """Create a tar.gz snapshot of the workdir; returns its opaque snapshot id."""
    workdir = workdir.resolve()
    snap_dir = _snapshot_dir()
    snap_dir.mkdir(parents=True, exist_ok=True)
    entries = list(_iter_workdir_files(workdir, snap_dir))
    current_files = {rel.as_posix(): metadata for _path, rel, metadata in entries}
    baseline_id = _find_baseline(workdir)
    baseline_files: dict = {}
    if baseline_id is not None:
        try:
            baseline = _load_manifest(baseline_id)
        except ValueError:
            baseline = None
        if baseline is not None and baseline.get("root") == _root_key(workdir):
            baseline_files = baseline["files"]
        else:
            baseline_id = None
    archive_names = {
        name
        for name, metadata in current_files.items()
        if baseline_id is None or baseline_files.get(name) != metadata
    }
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # Uniqueness must not depend on wall-clock seconds: two snapshots in the same
    # second must not overwrite each other.
    unique = uuid.uuid4().hex[:12]
    sid = f"{stamp}-{unique}"
    out = snap_dir / f"{sid}.tar.gz"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{sid}.", suffix=".tar.gz.partial", dir=str(snap_dir)
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            for path, rel, _metadata in entries:
                name = rel.as_posix()
                if name not in archive_names:
                    continue
                try:
                    tar.add(path, arcname=name)
                except (OSError, PermissionError):
                    current_files.pop(name, None)
        os.replace(tmp_path, out)
        try:
            _write_manifest(
                sid,
                {
                    "version": MANIFEST_VERSION,
                    "root": _root_key(workdir),
                    "baseline_snapshot_id": baseline_id,
                    "files": current_files,
                },
            )
        except (OSError, TypeError, ValueError):
            out.unlink(missing_ok=True)
            raise
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return sid


def _validated_tar_members(
    workdir: Path, tar: tarfile.TarFile
) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    for member in members:
        if "\x00" in member.name:
            raise ValueError(f"unsafe snapshot entry: {member.name}")
        if member.isfile() and member.size > MAX_SNAPSHOT_BYTES:
            raise ValueError(f"snapshot entry too large: {member.name}")
        try:
            destination = (workdir / member.name).resolve()
        except OSError as exc:
            raise ValueError(f"unsafe snapshot entry: {member.name}") from exc
        if not destination.is_relative_to(workdir):
            raise ValueError(f"unsafe snapshot entry: {member.name}")
    return members


def _manifest_archived_paths(workdir: Path, manifest: dict) -> set[str]:
    archived: set[str] = set()
    for name in manifest["files"]:
        parts = _manifest_name_parts(name)
        try:
            destination = workdir.joinpath(*parts).resolve()
        except OSError as exc:
            raise ValueError(f"unsafe snapshot entry: {name}") from exc
        if not destination.is_relative_to(workdir):
            raise ValueError(f"unsafe snapshot entry: {name}")
        archived.add(name)
    return archived


def _expected_archive_names(manifest: dict, baseline: dict | None) -> set[str]:
    if baseline is None:
        return set(manifest["files"])
    return {
        name
        for name, metadata in manifest["files"].items()
        if baseline["files"].get(name) != metadata
    }


def restore_workdir(workdir: Path, snapshot_id: str) -> dict:
    """Restore a snapshot over the workdir. Returns counts; refuses unknown/unsafe ids."""
    workdir = workdir.resolve()
    if not _valid_snapshot_id(snapshot_id):
        raise ValueError("invalid snapshot id")
    src = _snapshot_dir() / f"{snapshot_id}.tar.gz"
    if not src.is_file():
        raise FileNotFoundError(f"snapshot not found: {snapshot_id}")
    manifest = _load_manifest(snapshot_id)
    archive_ids = [snapshot_id]
    baseline_id = None
    baseline: dict | None = None
    if manifest is not None:
        if manifest["root"] != _root_key(workdir):
            raise ValueError("snapshot belongs to a different workdir")
        baseline_id = manifest["baseline_snapshot_id"]
        archived = _manifest_archived_paths(workdir, manifest)
        if baseline_id is not None:
            baseline = _load_manifest(baseline_id)
            if (
                baseline is None
                or baseline["root"] != manifest["root"]
                or baseline["baseline_snapshot_id"] is not None
            ):
                raise FileNotFoundError(f"snapshot baseline not found: {baseline_id}")
            _manifest_archived_paths(workdir, baseline)
            archive_ids.insert(0, baseline_id)
    restored = 0
    with ExitStack() as stack:
        opened: list[tuple[str, tarfile.TarFile, list[tarfile.TarInfo]]] = []
        for archive_id in archive_ids:
            archive_path = _snapshot_dir() / f"{archive_id}.tar.gz"
            if not archive_path.is_file():
                raise FileNotFoundError(f"snapshot baseline not found: {archive_id}")
            archive = stack.enter_context(tarfile.open(archive_path, "r:gz"))
            opened.append(
                (archive_id, archive, _validated_tar_members(workdir, archive))
            )
        if manifest is not None:
            for archive_id, _archive, members in opened:
                archive_manifest = baseline if archive_id == baseline_id else manifest
                comparison = None if archive_id == baseline_id else baseline
                actual = {member.name for member in members if member.isfile()}
                if actual != _expected_archive_names(archive_manifest, comparison):
                    raise ValueError(
                        f"snapshot archive does not match manifest: {archive_id}"
                    )
        for _archive_id, archive, members in opened:
            for member in members:
                if member.isfile():
                    archive.extract(member, workdir, filter="data")
                    restored += 1
        if manifest is None:
            archived = {
                member.name
                for _archive_id, _archive, members in opened
                for member in members
                if member.isfile()
            }
    removed = 0
    for path, rel, _metadata in _iter_workdir_files(workdir, _snapshot_dir()):
        if rel.as_posix() not in archived:
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
    return {
        "snapshot_id": snapshot_id,
        "restored_files": restored,
        "removed_new_files": removed,
    }


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
    manifest = _load_manifest(sid)
    if manifest is None:
        raise FileNotFoundError(f"snapshot manifest not found: {sid}")
    h = hashlib.sha256()
    h.update(manifest["root"].encode("utf-8"))
    for name in sorted(manifest["files"]):
        h.update(name.encode("utf-8"))
        h.update(json.dumps(manifest["files"][name], separators=(",", ":")).encode())
    return sid, h.hexdigest()
