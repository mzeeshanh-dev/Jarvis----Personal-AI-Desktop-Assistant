"""Centralized permission policy and interactive approvals.

Levels:
 0 read-only information          -> auto
 1 open apps / websites           -> auto
 2 read local files / inspect     -> auto (inside workspace roots; enforced by tools)
 3 create / modify                -> user policy (default: ask)
 4 shell commands / installs      -> ask (allowlisted commands may be auto)
 5 delete / send / irreversible   -> ALWAYS ask; cannot be overridden to auto
"""
from __future__ import annotations

import asyncio
import enum
import time
import uuid
from dataclasses import dataclass
from typing import Any

from core.config import AppSettings
from core.events import EventBus

LEVEL_NAMES = {
    0: "Read-only", 1: "Open apps & websites", 2: "Read local files", 3: "Create & modify",
    4: "Run commands", 5: "Irreversible",
}


class Decision(str, enum.Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PendingApproval:
    id: str
    task_id: str
    tool: str
    future: asyncio.Future[bool]
    created_at: float
    payload: dict[str, Any]


def evaluate(tool_name: str, level: int, settings: AppSettings, *, auto_ok: bool = False,
             tainted: bool = False) -> tuple[Decision, str]:
    perms = settings.permissions
    override = perms.tool_overrides.get(tool_name)
    if override == "deny":
        return Decision.DENY, "This tool is disabled in Settings → Permissions."
    if level >= 5:
        return Decision.ASK, "Irreversible action: always requires confirmation."
    if override == "ask":
        return Decision.ASK, "Tool policy requires confirmation."
    if tainted and level >= 3:
        return Decision.ASK, "This task read untrusted web/email/file content; confirming before changes."
    if override == "auto":
        return Decision.ALLOW, "Tool policy: automatic."
    if level <= 2:
        return Decision.ALLOW, LEVEL_NAMES[level]
    if level == 3:
        if perms.level3_policy == "auto":
            return Decision.ALLOW, "Level 3 policy: automatic."
        return Decision.ASK, "Creates or modifies data."
    if level == 4 and auto_ok and perms.allowlisted_commands_auto:
        return Decision.ALLOW, "Command is on the allowlist."
    return Decision.ASK, "Runs a command on your computer."


class ApprovalBroker:
    def __init__(self, events: EventBus) -> None:
        self.events = events
        self.pending: dict[str, PendingApproval] = {}

    async def request(self, *, task_id: str, tool: str, level: int, reason: str, summary: str,
                      arguments: dict[str, Any], timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        aid = uuid.uuid4().hex[:12]
        payload = {"id": aid, "task_id": task_id, "tool": tool, "level": level, "level_name": LEVEL_NAMES[level],
                   "reason": reason, "summary": summary, "arguments": arguments, "timeout_s": timeout_s}
        pa = PendingApproval(aid, task_id, tool, loop.create_future(), time.time(), payload)
        self.pending[aid] = pa
        await self.events.emit("approval.request", **payload)
        try:
            return await asyncio.wait_for(asyncio.shield(pa.future), timeout_s)
        except asyncio.TimeoutError:
            await self.events.emit("approval.expired", id=aid, task_id=task_id)
            return False
        finally:
            self.pending.pop(aid, None)

    def respond(self, approval_id: str, approved: bool) -> bool:
        pa = self.pending.get(approval_id)
        if not pa or pa.future.done():
            return False
        pa.future.set_result(bool(approved))
        return True

    def cancel_task(self, task_id: str) -> None:
        for pa in list(self.pending.values()):
            if pa.task_id == task_id and not pa.future.done():
                pa.future.set_result(False)

    def list(self) -> list[dict[str, Any]]:
        return [pa.payload for pa in self.pending.values()]
