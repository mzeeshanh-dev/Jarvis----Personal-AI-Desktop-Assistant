"""SQLite access. All queries run on one dedicated worker thread so the event
loop never blocks and the connection is never shared across threads."""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

from storage.migrations import MIGRATIONS

log = logging.getLogger(__name__)
T = TypeVar("T")


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        self._conn: sqlite3.Connection | None = None

    async def open(self) -> None:
        await self._run(self._open_sync)

    def _open_sync(self) -> None:
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._conn = conn
        self._migrate_sync()

    def _migrate_sync(self) -> None:
        assert self._conn
        c = self._conn
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        applied = {r[0] for r in c.execute("SELECT version FROM schema_migrations")}
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            log.info("applying migration %s", version)
            with self._tx_sync():
                for statement in [s.strip() for s in sql.split(";") if s.strip()]:
                    c.execute(statement)
                c.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    @contextmanager
    def _tx_sync(self) -> Iterator[sqlite3.Connection]:
        assert self._conn
        self._conn.execute("BEGIN")
        try:
            yield self._conn
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    async def execute(self, sql: str, params: tuple | dict = ()) -> int:
        def _do() -> int:
            assert self._conn
            cur = self._conn.execute(sql, params)
            return cur.lastrowid if cur.lastrowid else cur.rowcount
        return await self._run(_do)

    async def fetchall(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        def _do() -> list[dict[str, Any]]:
            assert self._conn
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]
        return await self._run(_do)

    async def fetchone(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = await self.fetchall(sql, params)
        return rows[0] if rows else None

    async def transaction(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run `fn(conn)` atomically on the DB thread."""
        def _do() -> T:
            with self._tx_sync() as conn:
                return fn(conn)
        return await self._run(_do)

    async def close(self) -> None:
        def _do() -> None:
            if self._conn:
                self._conn.close()
                self._conn = None
        await self._run(_do)
        self._executor.shutdown(wait=True)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
