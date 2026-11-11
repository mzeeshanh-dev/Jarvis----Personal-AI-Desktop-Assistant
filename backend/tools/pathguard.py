"""Workspace path restrictions for filesystem and terminal tools.

A path is allowed only if, after resolving symlinks/junctions and `..`, it lies
inside a configured workspace root AND outside protected system/app-data
locations AND is not a sensitive credential file.
"""
from __future__ import annotations

import os
from pathlib import Path

from agent.tool_registry import ToolError
from core.config import AppSettings, Paths

SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials",
    "credentials.json", "login data", "cookies", "web data", "key4.db", "logins.json", ".npmrc", ".pypirc",
    ".netrc", "_netrc", ".git-credentials", "token.json", "client_secret.json", "ntuser.dat",
}
SENSITIVE_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".kdbx", ".ovpn", ".ppk", ".keystore", ".jks")
SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".azure", ".kube", ".docker", ".gcloud", "microsoft\\credentials", "microsoft\\protect"}


def _env_path(var: str, *parts: str) -> Path | None:
    v = os.environ.get(var)
    return Path(v, *parts) if v else None


def protected_roots(paths: Paths) -> list[Path]:
    candidates = [
        _env_path("SystemRoot"), _env_path("ProgramFiles"), _env_path("ProgramFiles(x86)"), _env_path("ProgramData"),
        _env_path("APPDATA"), _env_path("LOCALAPPDATA"), _env_path("SystemDrive", "\\$Recycle.Bin"),
        _env_path("SystemDrive", "\\System Volume Information"), _env_path("SystemDrive", "\\Recovery"),
        paths.data,
    ]
    return [Path(os.path.realpath(p)) for p in candidates if p]


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return os.path.normcase(str(child)).startswith(os.path.normcase(str(parent)).rstrip("\\/") + os.sep) or \
            os.path.normcase(str(child)) == os.path.normcase(str(parent))


def workspace_roots(settings: AppSettings) -> list[Path]:
    return [Path(os.path.realpath(os.path.expandvars(os.path.expanduser(r)))) for r in settings.permissions.workspace_roots]


def is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES) or name.startswith(".env."):
        return True
    low = os.path.normcase(str(path))
    return any(f"\\{d}\\" in low + "\\" or f"/{d}/" in low + "/" for d in SENSITIVE_DIRS)


def resolve(raw: str, settings: AppSettings, paths: Paths, *, must_exist: bool = False, label: str = "path") -> Path:
    if not raw or not raw.strip():
        raise ToolError(f"{label} is empty", code="invalid_path")
    raw = raw.strip().strip('"')
    if raw.startswith("\\\\") or raw.startswith("//"):
        raise ToolError("Network (UNC) paths are not allowed.", code="path_denied")
    expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
    roots = workspace_roots(settings)
    if not roots:
        raise ToolError("No workspace folders are configured (Settings → Permissions).", code="path_denied")
    if not expanded.is_absolute():
        expanded = roots[0] / expanded
    real = Path(os.path.realpath(expanded))
    for prot in protected_roots(paths):
        if _within(real, prot):
            raise ToolError(f"Access to protected location {prot} is not allowed.", code="path_denied")
    if not any(_within(real, root) for root in roots):
        raise ToolError(
            f"{real} is outside the allowed workspace folders ({'; '.join(map(str, roots))}). "
            "Add the folder in Settings → Permissions to allow it.", code="path_denied")
    if is_sensitive(real):
        raise ToolError(f"{real.name} looks like a credential or secret file; access is blocked.", code="path_denied")
    if must_exist and not real.exists():
        raise ToolError(f"{real} does not exist.", code="not_found")
    return real
