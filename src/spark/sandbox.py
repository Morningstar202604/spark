from __future__ import annotations

import shlex
from pathlib import Path

from spark.config import SparkConfig
from spark.errors import PathEscapeError

DEFAULT_PROTECTED = ["/etc", "/root/.spark", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev"]

# Spark's own code and config: protected in every mode except unrestricted.
SELF_PROTECTED = ["/workspace/src/spark", "/root/.spark/config.toml"]

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
        return self.protected_paths + DEFAULT_PROTECTED + SELF_PROTECTED

    def _check_protected(self, path: Path) -> None:
        for raw in self._protected_effective():
            p = Path(raw)
            try:
                path.relative_to(p if p.is_absolute() else (self.root / p))
            except ValueError:
                continue
            raise SandboxPolicyError(f"Blocked protected path: {raw}")

    def _command_hits_protected(self, command: str) -> str | None:
        """Return the protected path entry hit by a path token in the command, if any."""
        for raw in self._protected_effective():
            p = (Path(raw) if Path(raw).is_absolute() else (self.root / raw)).resolve()
            for token in command.replace("|", " ").replace(";", " ").replace("&&", " ").split():
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
            tokens = shlex.split(lowered)
        except ValueError:
            tokens = lowered.split()
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
