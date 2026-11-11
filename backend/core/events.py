"""Fan-out of server events to every connected renderer WebSocket."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Sink(Protocol):
    async def send_json(self, data: Any) -> None: ...


class EventBus:
    def __init__(self) -> None:
        self._sinks: set[Sink] = set()
        self._listeners: list[asyncio.Queue[dict[str, Any]]] = []

    def add(self, sink: Sink) -> None:
        self._sinks.add(sink)

    def remove(self, sink: Sink) -> None:
        self._sinks.discard(sink)

    @property
    def client_count(self) -> int:
        return len(self._sinks)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """In-process listener (used by tests)."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._listeners.append(q)
        return q

    async def emit(self, type_: str, **payload: Any) -> None:
        event = {"type": type_, "ts": time.time(), **payload}
        for q in self._listeners:
            q.put_nowait(event)
        dead: list[Sink] = []
        for sink in list(self._sinks):
            try:
                await sink.send_json(event)
            except Exception:  # noqa: BLE001 - a closed socket must not break others
                dead.append(sink)
        for sink in dead:
            self._sinks.discard(sink)

    def emit_nowait(self, type_: str, **payload: Any) -> None:
        try:
            asyncio.get_running_loop().create_task(self.emit(type_, **payload))
        except RuntimeError:
            pass
