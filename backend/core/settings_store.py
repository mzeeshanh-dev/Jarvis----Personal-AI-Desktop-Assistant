"""Persisted, validated application settings."""
from __future__ import annotations

import time
from typing import Any, Callable

from core.config import AppSettings
from storage.database import Database, dumps, loads

KEY = "app_settings"


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("tool_overrides", "local_limits"):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.current = AppSettings()
        self._listeners: list[Callable[[AppSettings, AppSettings], Any]] = []

    async def load(self) -> AppSettings:
        row = await self.db.fetchone("SELECT value FROM settings WHERE key=?", (KEY,))
        if row:
            data = deep_merge(AppSettings().model_dump(), loads(row["value"], {}))
            try:
                self.current = AppSettings.model_validate(data)
            except Exception:  # noqa: BLE001 - corrupt settings fall back to defaults
                self.current = AppSettings()
        return self.current

    def on_change(self, fn: Callable[[AppSettings, AppSettings], Any]) -> None:
        self._listeners.append(fn)

    async def update(self, patch: dict[str, Any]) -> AppSettings:
        """Validate a partial update; raises pydantic.ValidationError on bad input."""
        merged = deep_merge(self.current.model_dump(), patch)
        new = AppSettings.model_validate(merged)
        await self.db.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (KEY, dumps(new.model_dump()), time.time()),
        )
        old, self.current = self.current, new
        for fn in self._listeners:
            res = fn(old, new)
            if hasattr(res, "__await__"):
                await res
        return new

    async def reset(self) -> AppSettings:
        await self.db.execute("DELETE FROM settings WHERE key=?", (KEY,))
        self.current = AppSettings()
        return self.current
