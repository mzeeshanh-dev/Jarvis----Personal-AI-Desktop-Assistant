"""Agent execution cycle.

instruction -> deterministic route?  --yes--> validate+permit+execute -> reply (0 LLM calls)
                 | no
                 v
           LLM (tools) -> tool calls -> validate+permit+execute (parallel when read-only)
                 ^                                 |
                 +---------- observe results ------+   ...until final answer / max iterations
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from agent.context_manager import ContextManager
from agent.conversation_manager import ConversationManager
from agent.executor import Executor, ToolCall
from agent.planner import DirectPlan, choose_model, route_direct, tool_categories
from agent.result_formatter import speakable, to_tool_message
from agent.services import Services
from agent.task_manager import TaskInfo, TaskManager
from agent.tool_registry import ToolContext, ToolRegistry, ToolResult
from providers.manager import NoProviderAvailable, ProviderError

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, services: Services, registry: ToolRegistry, tasks: TaskManager) -> None:
        self.s = services
        self.registry = registry
        self.executor = Executor(registry)
        self.conversations = ConversationManager(services.db)
        self.context = ContextManager(self.conversations)
        self.tasks = tasks

    def submit(self, text: str, conversation_id: str | None = None, source: str = "text") -> TaskInfo:
        text = text.strip()
        if not text:
            raise ValueError("empty instruction")
        return self.tasks.submit(text[:8000], conversation_id, source, self._run)

    async def _run(self, info: TaskInfo) -> None:
        s = self.s
        settings = s.settings
        started = time.perf_counter()
        cid = await self.conversations.ensure(info.conversation_id, info.text)
        info.conversation_id = cid
        await s.db.execute("INSERT INTO tasks(id, conversation_id, input, source, status, started_at) VALUES (?,?,?,?,?,?)",
                           (info.id, cid, info.text, info.source, "running", time.time()))
        await s.events.emit("task.started", task_id=info.id, conversation_id=cid, text=info.text, source=info.source)
        await self.conversations.add(cid, "user", info.text, task_id=info.id)
        ctx = ToolContext(info.id, cid, settings, s)
        route, status, error = "direct", "completed", None
        counters = {"llm": 0, "tools": 0}
        final: str | None = None
        try:
            plan = route_direct(info.text, settings)
            if plan:
                final = await self._direct(plan, ctx, counters)
            if final is None:
                route = "llm"
                final = await self._llm_loop(info, ctx, counters)
        except asyncio.CancelledError:
            await self._finish(info, "Stopped.", "cancelled", route, counters, started, None, speak=False)
            raise
        except NoProviderAvailable as exc:
            final, status, error = exc.user_message(), "failed", str(exc)
        except ProviderError as exc:
            final, status, error = f"I couldn't complete that because the AI service returned an error: {exc}", "failed", str(exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("orchestrator failure")
            final, status, error = "Something went wrong while handling that request. Details are in Settings → Logs.", "failed", repr(exc)
        await self._finish(info, final or "Done.", status, route, counters, started, error)

    async def _finish(self, info: TaskInfo, text: str, status: str, route: str, counters: dict[str, int],
                      started: float, error: str | None, speak: bool = True) -> None:
        s = self.s
        cid = info.conversation_id
        assert cid
        await asyncio.shield(self._persist_finish(info, text, status, route, counters, error))
        e2e = (time.perf_counter() - started) * 1000
        s.perf.record("e2e_ms", e2e)
        await s.events.emit("assistant.message", task_id=info.id, conversation_id=cid, content=text, status=status)
        await s.events.emit("task.finished", task_id=info.id, conversation_id=cid, status=status, route=route,
                            llm_calls=counters["llm"], tool_calls=counters["tools"], duration_ms=round(e2e))
        settings = s.settings
        if speak and s.voice and settings.voice.tts_engine != "off" and (
                (info.source == "voice" and settings.voice.speak_responses) or settings.voice.speak_typed_responses):
            await s.voice.speak(speakable(text), task_id=info.id)

    async def _persist_finish(self, info: TaskInfo, text: str, status: str, route: str, counters: dict[str, int], error: str | None) -> None:
        assert info.conversation_id
        await self.conversations.add(info.conversation_id, "assistant", text, task_id=info.id)
        await self.s.db.execute(
            "UPDATE tasks SET status=?, route=?, error=?, result=?, llm_calls=?, tool_calls=?, finished_at=? WHERE id=?",
            (status, route, error, text[:2000], counters["llm"], counters["tools"], time.time(), info.id))

    # ------------------------------------------------------------- direct
    async def _direct(self, plan: DirectPlan, ctx: ToolContext, counters: dict[str, int]) -> str | None:
        await self.s.events.emit("task.status", task_id=ctx.task_id, phase="executing", detail=plan.tool)
        result = await self.executor.run(ToolCall(f"direct-{uuid.uuid4().hex[:8]}", plan.tool, plan.arguments), ctx)
        counters["tools"] += 1
        if result.ok:
            if plan.intent == "web_search":
                return f"Searching {plan.meta['engine'].title()} for “{plan.meta['query']}”."
            return result.summary
        if result.code in ("declined", "denied") or not plan.fallback_to_llm:
            return result.error
        # e.g. "open my resume" is not an app: let the LLM use other tools.
        return None if self._llm_configured() else result.error

    def _llm_configured(self) -> bool:
        return any(p.enabled for p in self.s.providers.providers.values())

    # ---------------------------------------------------------------- LLM
    async def _llm_loop(self, info: TaskInfo, ctx: ToolContext, counters: dict[str, int]) -> str:
        s = self.s
        settings = s.settings
        assert info.conversation_id
        messages = await self.context.build(info.conversation_id, settings)
        prior = {c for m in messages for tc in m.get("tool_calls") or []
                 if (c := self.registry.category_of(tc.get("function", {}).get("name", "")))}
        recent_user = " ".join(m["content"] for m in messages if m["role"] == "user")[-1500:]
        categories = tool_categories(f"{recent_user} {info.text}", prior)
        tools = self.registry.schemas(categories)
        model, tier = choose_model(info.text, settings)
        corrected = False
        nudged = False
        max_iter = settings.automation.max_iterations
        for iteration in range(max_iter):
            await s.events.emit("task.status", task_id=info.id, phase="thinking", detail=model, iteration=iteration)
            stream_id = f"{info.id}-{iteration}"

            async def on_delta(text: str, _sid: str = stream_id) -> None:
                await s.events.emit("assistant.delta", task_id=info.id, conversation_id=info.conversation_id, stream_id=_sid, text=text)

            try:
                res = await s.groq.chat(model=model, messages=messages, tools=tools, on_delta=on_delta,
                                        reasoning_effort=settings.providers.reasoning_effort,
                                        # Groq counts requested completion tokens toward TPM; keep this modest.
                                        max_tokens=1536 if tier == "complex" else 1024)
            except ProviderError as exc:
                if exc.status == 400 and "tool" in str(exc).lower():
                    if tier == "simple":
                        model, tier = settings.providers.models.complex, "complex"
                        continue
                    if not corrected:
                        corrected = True
                        messages.append({"role": "system", "content": "Your previous tool call was malformed. Call tools with valid JSON arguments matching their schema."})
                        continue
                raise
            counters["llm"] += 1
            s.perf.record("llm_ms", res.latency_ms)

            if not res.tool_calls:
                if res.content.strip():
                    return res.content.strip()
                if not nudged:
                    # Reasoning models occasionally spend the whole budget thinking and return nothing.
                    nudged = True
                    messages.append({"role": "system", "content": "Now answer the user directly in plain text, briefly, using the tool results above."})
                    continue
                return "Done."

            for tc in res.tool_calls:
                tc["id"] = tc.get("id") or f"call_{uuid.uuid4().hex[:10]}"
            assistant_msg = {"role": "assistant", "content": res.content or "", "tool_calls": res.tool_calls}
            messages.append(assistant_msg)
            await self.conversations.add(info.conversation_id, "assistant", res.content or None, tool_calls=res.tool_calls, task_id=info.id)

            calls = [ToolCall(tc["id"], tc["function"]["name"], tc["function"].get("arguments") or "{}") for tc in res.tool_calls]
            await s.events.emit("task.status", task_id=info.id, phase="executing", detail=", ".join(c.name for c in calls))
            results = await self._execute(calls, ctx)
            counters["tools"] += len(calls)
            limit = settings.performance.tool_result_char_limit
            for call, result in zip(calls, results):
                content = to_tool_message(result, limit)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
                await self.conversations.add(info.conversation_id, "tool", content, tool_call_id=call.id, name=call.name, task_id=info.id)
        return f"I stopped after {max_iter} steps without finishing. You can raise the step limit in Settings → Desktop automation, or rephrase the request."

    async def _execute(self, calls: list[ToolCall], ctx: ToolContext) -> list[ToolResult]:
        settings = self.s.settings
        read_only = all(self.executor.level_for(c, ctx) <= 2 for c in calls)
        if settings.automation.parallel_tools and len(calls) > 1 and read_only:
            return list(await asyncio.gather(*(self.executor.run(c, ctx) for c in calls)))
        return [await self.executor.run(c, ctx) for c in calls]
