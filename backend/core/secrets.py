"""OS-backed secret storage (Windows Credential Manager via `keyring`).

Only opaque references (e.g. ``groq:3f2a…``) are stored in SQLite; the secret
values themselves never touch the database, logs or the renderer.
"""
from __future__ import annotations

import asyncio
import logging
import re

log = logging.getLogger(__name__)

SERVICE = "Jarvis Desktop Agent"


class SecretStore:
    def __init__(self, service: str = SERVICE) -> None:
        self.service = service
        import sys

        import keyring  # lazy: keeps import cost off the hot path

        if sys.platform == "win32":
            # Pin Windows Credential Manager explicitly: entry-point discovery
            # can silently pick a no-op backend inside a PyInstaller bundle.
            from keyring.backends.Windows import WinVaultKeyring
            keyring.set_keyring(WinVaultKeyring())
        self._keyring = keyring

    async def get(self, ref: str) -> str | None:
        return await asyncio.to_thread(self._keyring.get_password, self.service, ref)

    async def set(self, ref: str, value: str) -> None:
        await asyncio.to_thread(self._keyring.set_password, self.service, ref, value)

    async def delete(self, ref: str) -> None:
        def _del() -> None:
            try:
                self._keyring.delete_password(self.service, ref)
            except self._keyring.errors.PasswordDeleteError:
                pass
        await asyncio.to_thread(_del)


class MemorySecretStore(SecretStore):
    """In-memory store used by tests."""

    def __init__(self) -> None:  # noqa: D401 - no keyring
        self.service = "memory"
        self._data: dict[str, str] = {}

    async def get(self, ref: str) -> str | None:
        return self._data.get(ref)

    async def set(self, ref: str, value: str) -> None:
        self._data[ref] = value

    async def delete(self, ref: str) -> None:
        self._data.pop(ref, None)


_PATTERNS = [
    re.compile(r"gsk_[A-Za-z0-9]{8,}"),
    re.compile(r"([?&]token=)[^&\s\"']+"),  # launch token in WebSocket/media URLs (uvicorn access lines)
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"ya29\.[A-Za-z0-9._\-]+"),
    re.compile(r"1//[A-Za-z0-9._\-]{20,}"),
    re.compile(r"(?i)(\"?(?:api[_-]?key|client_secret|refresh_token|access_token|password)\"?\s*[:=]\s*\"?)[^\s\",}]+"),
]


def redact(text: str) -> str:
    """Strip anything that looks like a credential from a string."""
    if not text:
        return text
    out = text
    for pat in _PATTERNS:
        out = pat.sub(lambda m: (m.group(1) if m.groups() else "") + "[REDACTED]", out)
    return out


def key_hint(secret: str) -> str:
    return f"{secret[:4]}…{secret[-4:]}" if len(secret) > 12 else "…"


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = redact(msg)
        record.args = ()
        return True
