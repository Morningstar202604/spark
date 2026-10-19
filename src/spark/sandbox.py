"""Workdir sandbox with path-escape and command-injection guards."""
from __future__ import annotations

import os
import shlex
from pathlib import Path

from spark.config import SparkConfig, default_home
from spark.errors import PathEscapeError

_WINDOWS_RESERVED = {"con", "prn", "aux", "nul"}


def safe_bare_filename(filename: str) -> str | None:
    """接受"工作目录内的裸文件名"，拒绝任何可能逃逸的路径形态。

    拒绝：空/带首尾空白/``.``/``..``/超长、含分隔符或盘符或 NUL、任何含
    ``..`` 的名字、Windows 设备名（con/prn/aux/nul）。合法时原样返回。
    """
    if not filename or filename != filename.strip():
        return None
    if filename in {".", ".."} or len(filename) > 255:
        return None
    if any(sep in filename for sep in ("/", "\\", ":", "\x00")):
        return None
    if ".." in filename:
        return None
    stem = filename.split(".")[0].lower()
    if stem in _WINDOWS_RESERVED:
        return None
    return filename


# ``_logical()`` strips the drive letter, so these POSIX-shaped entries also
# match C:/Windows, D:/Program Files and friends on Windows.
DEFAULT_PROTECTED = [
    "/etc",
    "/root/.spark",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/proc",
    "/sys",
    "/dev",
    "/windows",
    "/program files",
    "/program files (x86)",
]

# Spark's own code and config: protected in every mode except unrestricted.


def self_protected() -> list[str]:
    """Paths Spark must never let a tool write to.

    These used to be hardcoded container paths (``/workspace/src/spark``) that
    matched nothing on a real machine, so "the agent protects its own code" was
    silently a no-op. Resolved per call so ``SPARK_HOME`` overrides apply.
    """
    import spark as _spark_pkg

    paths = [str(Path(_spark_pkg.__file__).resolve().parent)]
    config_toml = default_home() / "config.toml"
    paths.append(str(config_toml))
    return paths

# Commands that read sensitive material or modify system state; blocked outside full-access.
DENY_PATTERNS = (
    "sudo",
    "su ",
    "su\t",
    "shutdown",
    "reboot",
    "poweroff",
    "mkfs",
    "fdisk",
    "dd if=",
    "iptables",
    "passwd",
    "visudo",
    "systemctl enable",
    "systemctl disable",
    "curl http://169.254.169.254",
    "rm -rf /",
    "rm -rf ~",
    "rm -rf $home",
    "/etc/shadow",
    # Git history rewrite / remote side effects. The system prompt promises the
    # agent never runs these, so the promise has to be enforced, not aspirational.
    "git push",
    "git pull",
    "git reset --hard",
    "git clean -f",
    "git checkout .",
    "git restore .",
    # Windows-native equivalents of the destructive POSIX commands above; the
    # list used to be POSIX-only, so it did not protect this platform at all.
    "remove-item -recurse",
    "ri -recurse",
    "del /s",
    "rd /s",
    "rmdir /s",
    "format ",
    "net user ",
    "reg delete",
    "vssadmin delete",
)


class SandboxPolicyError(PermissionError):
    pass


class WorkdirSandbox:
    def __init__(self, workdir: Path, cfg: SparkConfig | None = None) -> None:
        self.root = workdir.resolve()
        self.cfg = cfg
        self.sandbox_mode = getattr(cfg.agent, "sandbox_mode", "workspace") if cfg else "workspace"
        self.protected_paths = list(getattr(cfg.agent, "protected_paths", []) or []) if cfg else []

    @property
    def shell_allowed(self) -> bool:
        return self.sandbox_mode in {"workspace", "full-access", "unrestricted"}

    @property
    def write_allowed(self) -> bool:
        return self.sandbox_mode in {"workspace", "full-access", "unrestricted"}

    @property
    def full_access(self) -> bool:
        return self.sandbox_mode in {"full-access", "unrestricted"}

    @property
    def unrestricted(self) -> bool:
        return self.sandbox_mode == "unrestricted"

    def resolve(self, rel: str) -> Path:
        raw = rel.strip() or "."
        path = (self.root / raw).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            if self.full_access:
                self._check_protected(path)
                return path
            raise PathEscapeError(f"Path escapes workdir: {rel}") from exc
        self._check_protected(path)
        return path

    def _protected_effective(self) -> list[str]:
        if self.unrestricted:
            return self.protected_paths
        return self.protected_paths + DEFAULT_PROTECTED + self_protected()

    @staticmethod
    def _logical(path: Path) -> Path:
        # Drive-stripped form so POSIX-style absolute rules still match on Windows.
        if path.drive:
            return Path("/") / path.relative_to(path.anchor)
        return path

    def _check_protected(self, path: Path) -> None:
        for raw in self._protected_effective():
            p = Path(raw)
            base = p if (p.is_absolute() or raw[:1] in ("/", "\\")) else (self.root / p)
            try:
                self._logical(path).relative_to(self._logical(base))
            except ValueError:
                continue
            raise SandboxPolicyError(f"Blocked protected path: {raw}")

    def is_path_allowed(self, path: Path) -> bool:
        """True 当 *path* 未命中保护路径清单（供只读遍历工具过滤候选文件）。"""
        try:
            self._check_protected(path)
        except SandboxPolicyError:
            return False
        return True

    def _command_tokens(self, command: str) -> list[str]:
        """Sanitise a shell command into safe tokens.

        Replaces shell metacharacters, command substitutions, and chaining
        operators with spaces before tokenising. Best-effort — not a security
        boundary; the real boundary is ``check_shell`` which decides allow/deny.
        """
        cleaned = command
        # Block-process substitution: <(cmd) and >(cmd)
        for pat in ("<(", ">(", "$(", "${"):
            cleaned = cleaned.replace(pat, " ")
        # Command chaining and redirection
        for ch in ("|", ";", "&&", "||", "\n", "\r", "\t"):
            cleaned = cleaned.replace(ch, " ")
        # Backticks and bare $
        cleaned = cleaned.replace("`", " ").replace("$", " ")
        try:
            tokens = shlex.split(cleaned, posix=os.name != "nt")
        except ValueError:
            tokens = cleaned.split()
        return [tok for tok in tokens if tok]

    def _command_hits_protected(
        self, command: str, paths: list[str] | None = None
    ) -> str | None:
        """Return the protected path entry hit by a path token in the command, if any.

        ``paths`` 缺省为生效清单（含内置凭据保护）；unrestricted 档传入用户清单。
        """
        for raw in (paths if paths is not None else self._protected_effective()):
            p = (Path(raw) if Path(raw).is_absolute() else (self.root / raw)).resolve()
            for token in self._command_tokens(command):
                try:
                    cand = Path(token)
                    if cand.is_absolute():
                        cand = cand.resolve()
                    else:
                        cand = (self.root / cand).resolve()
                except (OSError, ValueError):
                    continue
                try:
                    cand.relative_to(p)
                    return raw
                except ValueError:
                    continue
        return None

    def check_shell(self, command: str) -> str | None:
        """Return a rejection reason, or None when the command may run."""
        if self.unrestricted:
            hit_user = self._command_hits_protected(command, self.protected_paths)
            if hit_user:
                return f"command blocked: references protected path {hit_user}"
            return None
        if self.full_access:
            hit = self._command_hits_protected(command)
            if hit:
                return f"command blocked: references protected path {hit}"
            return None
        lowered = command.strip().lower()
        for pattern in DENY_PATTERNS:
            if pattern in lowered:
                return f"command blocked by sandbox policy (pattern: {pattern.strip()})"
        try:
            tokens = shlex.split(lowered, posix=os.name != "nt")
        except ValueError:
            tokens = lowered.split()
        tokens = [tok.strip("\"'") for tok in tokens]
        for token in tokens:
            base = token.rsplit("/", 1)[-1]
            if base in {"env", "printenv"}:
                return f"command blocked: reading environment via '{base}' is not allowed"
        if any(p in lowered for p in ("/proc/self/environ", "id_rsa", ".ssh/", ".aws/", ".netrc")):
            return "command blocked: access to credential paths is not allowed"
        hit = self._command_hits_protected(command)
        if hit:
            return f"command blocked: references protected path {hit}"
        return None

    def check_write_path(self, path: Path) -> str | None:
        if self.unrestricted:
            try:
                self._check_protected(path)
            except SandboxPolicyError as exc:
                return str(exc)
            return None
        try:
            path.relative_to(self.root)
        except ValueError:
            if self.full_access:
                try:
                    self._check_protected(path)
                except SandboxPolicyError as exc:
                    return str(exc)
                return None
            return f"write blocked outside workdir: {path}"
        try:
            self._check_protected(path)
        except SandboxPolicyError as exc:
            return str(exc)
        return None
