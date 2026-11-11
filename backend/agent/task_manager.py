"""Tracks running agent tasks; supports cancellation and a concurrency cap."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from agent.permissions import ApprovalBroker
from core.events import EventBus

log = logging.getLogger(__name__)


@dataclass
class TaskInfo:
    id: str
    conversation_id: str | None
    text: str
    source: str
    started_at: float
    task: asyncio.Task[None] | None = None
    phase: str = "queued"


class TaskManager:
    def __init__(self, events: EventBus, approvals: ApprovalBroker, max_concurrent: int = 2) -> None:
        self.events = events
        self.approvals = approvals
        self.tasks: dict[str, TaskInfo] = {}
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max = max_concurrent

    def set_limit(self, n: int) -> None:
        if n != self._max:
            self._sem = asyncio.Semaphore(n)
            self._max = n

    def submit(self, text: str, conversation_id: str | None, source: str,
               runner: Callable[[TaskInfo], Awaitable[None]]) -> TaskInfo:
        info = TaskInfo(uuid.uuid4().hex[:16], conversation_id, text, source, time.time())
        sem = self._sem

        async def wrapped() -> None:
            try:
                async with sem:
                    info.phase = "running"
                    await runner(info)
            except asyncio.CancelledError:
                await self.events.emit("task.cancelled", task_id=info.id, conversation_id=info.conversation_id)
            except Exception:  # noqa: BLE001
                log.exception("task %s crashed", info.id)
            finally:
                self.tasks.pop(info.id, None)

        self.tasks[info.id] = info
        info.task = asyncio.create_task(wrapped(), name=f"task-{info.id}")
        return info

    def cancel(self, task_id: str) -> bool:
        info = self.tasks.get(task_id)
        if not info or not info.task:
            return False
        # Cancel first: resolving the pending approval first races with
        # asyncio.wait_for on 3.11 and can swallow the cancellation. The
        # broker removes the approval in its own finally block.
        info.task.cancel()
        return True

    def cancel_all(self) -> int:
        n = 0
        for tid in list(self.tasks):
            n += int(self.cancel(tid))
        return n

    def active(self) -> list[dict]:
        return [{"id": t.id, "conversation_id": t.conversation_id, "text": t.text, "source": t.source,
                 "phase": t.phase, "started_at": t.started_at} for t in self.tasks.values()]

    async def shutdown(self) -> None:
        tasks = [t.task for t in self.tasks.values() if t.task]
        self.cancel_all()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
