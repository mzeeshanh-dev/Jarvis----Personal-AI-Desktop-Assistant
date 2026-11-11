"""Validated, permission-checked, time-limited tool execution.

The model only *requests* tools; nothing here trusts its arguments: they are
parsed, validated against the tool's Pydantic model (unknown fields rejected),
assessed for their real permission level, and approved before running.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from agent import permissions
from agent.result_formatter import preview, preview_args
from agent.tool_registry import Assessment, ToolContext, ToolError, ToolRegistry, ToolResult
from core.secrets import redact
from storage.database import dumps

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str | dict[str, Any]


class Executor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def level_for(self, call: ToolCall, ctx: ToolContext) -> int:
        tool = self.registry.get(call.name)
        if not tool:
            return 5
        try:
            args = tool.input_model.model_validate(_parse(call.arguments))
        except (ValidationError, ValueError):
            return tool.level
        return tool.assess(args, ctx).level if tool.assess else tool.level

    async def run(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        services = ctx.services
        started = time.perf_counter()
        tool = self.registry.get(call.name)
        if tool is None or not self.registry.is_available(call.name):
            return ToolResult.failure(f"Unknown or unavailable tool '{call.name}'.", "unknown_tool")

        try:
            raw = _parse(call.arguments)
            args = tool.input_model.model_validate(raw)
        except ValueError as exc:
            msg = _validation_message(exc)
            await self._log(ctx, tool.name, {"raw": str(call.arguments)[:500]}, tool.level, "rejected", "invalid", started, error=msg)
            return ToolResult.failure(f"Invalid arguments for {tool.name}: {msg}", "invalid_arguments")

        try:
            assessment = tool.assess(args, ctx) if tool.assess else Assessment(tool.level)
        except ToolError as exc:
            await self._log(ctx, tool.name, raw, tool.level, "rejected", "blocked", started, error=str(exc))
            return ToolResult.failure(str(exc), exc.code)
        level = max(tool.level, assessment.level)
        decision, reason = permissions.evaluate(tool.name, level, ctx.settings, auto_ok=assessment.auto_ok, tainted=ctx.tainted)
        if assessment.reason and decision is not permissions.Decision.ALLOW:
            reason = f"{reason} {assessment.reason}"
        shown_args = preview_args(args.model_dump())

        if decision is permissions.Decision.DENY:
            await self._log(ctx, tool.name, shown_args, level, "denied", "denied", started)
            return ToolResult.failure(f"{tool.name} is blocked by your permission settings.", "denied")
        if decision is permissions.Decision.ASK:
            await services.events.emit("task.status", task_id=ctx.task_id, phase="awaiting_approval", detail=tool.name)
            approved = await services.approvals.request(
                task_id=ctx.task_id, tool=tool.name, level=level, reason=reason,
                summary=tool.describe(args) if tool.describe else tool.description.split(".")[0],
                arguments=shown_args, timeout_s=ctx.settings.permissions.approval_timeout_s)
            if not approved:
                await self._log(ctx, tool.name, shown_args, level, "declined", "declined", started)
                return ToolResult.failure("The user declined this action.", "declined")
            decision_label = "approved"
        else:
            decision_label = "auto"

        timeout = tool.timeout_s or ctx.settings.automation.tool_timeout_s
        await services.events.emit("tool.started", task_id=ctx.task_id, call_id=call.id, tool=tool.name,
                                   level=level, arguments=shown_args)
        attempts = 2 if tool.idempotent else 1
        result: ToolResult | None = None
        for attempt in range(attempts):
            try:
                result = await asyncio.wait_for(tool.handler(args, ctx), timeout)
                break
            except asyncio.TimeoutError:
                result = ToolResult.failure(f"{tool.name} timed out after {timeout:.0f}s.", "timeout")
            except ToolError as exc:
                result = ToolResult.failure(str(exc), exc.code)
                if not (exc.transient and attempt + 1 < attempts):
                    break
            except asyncio.CancelledError:
                await self._log(ctx, tool.name, shown_args, level, decision_label, "cancelled", started)
                raise
            except Exception as exc:  # noqa: BLE001 - never crash the agent on a tool bug
                log.exception("tool %s crashed", tool.name)
                result = ToolResult.failure(f"{tool.name} failed: {type(exc).__name__}: {redact(str(exc))[:300]}", "internal")
                break
        assert result is not None
        if result.ok and result.untrusted:
            ctx.tainted = True
            ctx.tainted_by.append(tool.name)

        duration = (time.perf_counter() - started) * 1000
        services.perf.record("tool_ms", duration)
        await services.events.emit("tool.finished", task_id=ctx.task_id, call_id=call.id, tool=tool.name,
                                   ok=result.ok, summary=preview(result), duration_ms=round(duration))
        await self._log(ctx, tool.name, shown_args, level, decision_label, "ok" if result.ok else (result.code or "error"),
                        started, preview=preview(result), error=result.error)
        return result

    async def _log(self, ctx: ToolContext, tool: str, args: dict[str, Any], level: int, decision: str, status: str,
                   started: float, *, preview: str | None = None, error: str | None = None) -> None:
        try:
            await ctx.services.db.execute(
                "INSERT INTO execution_logs(task_id, tool, arguments, level, decision, status, duration_ms, result_preview, error, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ctx.task_id, tool, redact(dumps(args))[:4000], level, decision, status,
                 (time.perf_counter() - started) * 1000, preview, redact(error)[:1000] if error else None, time.time()))
        except Exception:  # noqa: BLE001
            log.exception("failed to write execution log")


def _parse(arguments: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    text = (arguments or "").strip() or "{}"
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"arguments are not valid JSON ({exc.msg})") from exc
    if not isinstance(value, dict):
        raise ValueError("arguments must be a JSON object")
    return value


def _validation_message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in exc.errors()[:5])
    return str(exc)
