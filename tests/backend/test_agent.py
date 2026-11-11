"""Agent: planner routing, permissions, argument validation, approvals,
timeouts, orchestrator tool-calling loop, cancellation, context building."""
from __future__ import annotations

import asyncio
import json

import pytest

from agent import permissions
from agent.context_manager import _drop_orphans, _trim
from agent.executor import Executor, ToolCall
from agent.permissions import ApprovalBroker, Decision
from agent.planner import choose_model, route_direct
from agent.tool_registry import ToolContext, ToolInput, ToolRegistry, ToolResult
from conftest import text_completion, tool_completion
from core.config import AppSettings


# ---------------------------------------------------------------- planner
@pytest.mark.parametrize("text,tool,arg", [
    ("Open Chrome.", "open_application", ("name", "Chrome")),
    ("Jarvis, please open VS Code", "open_application", ("name", "VS Code")),
    ("launch spotify", "open_application", ("name", "spotify")),
    ("open youtube", "open_url", ("url", "https://www.youtube.com")),
    ("open github.com", "open_url", ("url", "https://github.com")),
    ("go to news.ycombinator.com", "open_url", ("url", "https://news.ycombinator.com")),
])
def test_direct_routes(text, tool, arg):
    plan = route_direct(text, AppSettings())
    assert plan and plan.tool == tool and plan.arguments[arg[0]] == arg[1]


def test_direct_search():
    plan = route_direct("search google for ryzen 5 5500u benchmarks", AppSettings())
    assert plan and plan.intent == "web_search" and "q=ryzen+5+5500u+benchmarks" in plan.arguments["url"]


@pytest.mark.parametrize("text", [
    "find GreenBot emails and summarize them",
    "search my files for the invoice",
    "open chrome and search for cats",
    "open my latest downloaded pdf",
    "what's the weather like",
    "search emails for greenbot",
])
def test_complex_goes_to_llm(text):
    assert route_direct(text, AppSettings()) is None


def test_model_choice():
    s = AppSettings()
    assert choose_model("list my running apps", s)[1] == "simple"
    assert choose_model("find GreenBot emails, compare their latest updates and tell me which tasks are pending", s)[1] == "complex"


# ------------------------------------------------------------ permissions
def test_permission_levels(settings):
    ev = permissions.evaluate
    assert ev("t", 0, settings)[0] is Decision.ALLOW
    assert ev("t", 2, settings)[0] is Decision.ALLOW
    assert ev("t", 3, settings)[0] is Decision.ASK
    assert ev("t", 4, settings)[0] is Decision.ASK
    assert ev("t", 4, settings, auto_ok=True)[0] is Decision.ALLOW
    settings.permissions.level3_policy = "auto"
    assert ev("t", 3, settings)[0] is Decision.ALLOW
    assert ev("t", 3, settings, tainted=True)[0] is Decision.ASK  # prompt-injection guard
    settings.permissions.tool_overrides = {"t": "auto", "x": "deny"}
    assert ev("t", 5, settings)[0] is Decision.ASK  # level 5 can never be auto
    assert ev("x", 0, settings)[0] is Decision.DENY


# --------------------------------------------------------------- executor
class EchoIn(ToolInput):
    text: str
    times: int = 1


class FakePerf:
    def record(self, *_a): pass


class FakeServices:
    def __init__(self, db, events):
        self.db, self.events = db, events
        self.approvals = ApprovalBroker(events)
        self.perf = FakePerf()


def _registry():
    reg = ToolRegistry()

    @reg.tool("echo", "Echo text.", input_model=EchoIn, level=0)
    async def echo(a: EchoIn, ctx):
        return ToolResult.success(a.text * a.times)

    @reg.tool("write_thing", "Level 3 tool.", input_model=EchoIn, level=3)
    async def write_thing(a: EchoIn, ctx):
        return ToolResult.success("written")

    @reg.tool("slow", "Sleeps.", input_model=EchoIn, level=0, timeout_s=0.05)
    async def slow(a: EchoIn, ctx):
        await asyncio.sleep(1)
        return ToolResult.success("late")

    @reg.tool("web", "Untrusted.", input_model=EchoIn, level=0)
    async def web(a: EchoIn, ctx):
        return ToolResult.success("page", untrusted=True, source="web")
    return reg


async def test_executor_validates_arguments(db, events, settings):
    ex = Executor(_registry())
    ctx = ToolContext("t1", None, settings, FakeServices(db, events))
    assert (await ex.run(ToolCall("1", "echo", '{"text": "ab", "times": 2}'), ctx)).summary == "abab"
    bad_type = await ex.run(ToolCall("2", "echo", '{"text": "a", "times": "many"}'), ctx)
    assert not bad_type.ok and bad_type.code == "invalid_arguments"
    extra = await ex.run(ToolCall("3", "echo", '{"text": "a", "rm_rf": true}'), ctx)
    assert not extra.ok and "rm_rf" in extra.error
    not_json = await ex.run(ToolCall("4", "echo", "{text: a"), ctx)
    assert not not_json.ok
    unknown = await ex.run(ToolCall("5", "format_disk", "{}"), ctx)
    assert unknown.code == "unknown_tool"
    logs = await db.fetchall("SELECT status FROM execution_logs")
    assert {r["status"] for r in logs} >= {"ok", "invalid"}


async def test_executor_approval_flow(db, events, settings):
    ex = Executor(_registry())
    services = FakeServices(db, events)
    ctx = ToolContext("t1", None, settings, services)
    q = events.subscribe()

    async def approve_when_asked(answer: bool):
        while True:
            e = await q.get()
            if e["type"] == "approval.request":
                services.approvals.respond(e["id"], answer)
                return
    responder = asyncio.create_task(approve_when_asked(True))
    r = await ex.run(ToolCall("1", "write_thing", '{"text": "x"}'), ctx)
    await responder
    assert r.ok
    responder = asyncio.create_task(approve_when_asked(False))
    r = await ex.run(ToolCall("2", "write_thing", '{"text": "x"}'), ctx)
    await responder
    assert not r.ok and r.code == "declined"


async def test_approval_timeout_denies(db, events, settings):
    settings.permissions.approval_timeout_s = 10
    services = FakeServices(db, events)
    res = await services.approvals.request(task_id="t", tool="x", level=3, reason="", summary="", arguments={}, timeout_s=0.05)
    assert res is False


async def test_tool_timeout(db, events, settings):
    ctx = ToolContext("t1", None, settings, FakeServices(db, events))
    r = await Executor(_registry()).run(ToolCall("1", "slow", '{"text": "x"}'), ctx)
    assert not r.ok and r.code == "timeout"


async def test_untrusted_output_taints_context(db, events, settings):
    settings.permissions.level3_policy = "auto"
    services = FakeServices(db, events)
    ctx = ToolContext("t1", None, settings, services)
    ex = Executor(_registry())
    q = events.subscribe()
    await ex.run(ToolCall("1", "web", '{"text": "x"}'), ctx)
    assert ctx.tainted
    task = asyncio.create_task(ex.run(ToolCall("2", "write_thing", '{"text": "x"}'), ctx))
    while (e := await q.get())["type"] != "approval.request":
        pass
    assert "untrusted" in e["reason"]
    services.approvals.respond(e["id"], False)
    assert not (await task).ok


# ---------------------------------------------------------------- context
def test_context_drops_orphan_tool_calls():
    msgs = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}, {"id": "c2"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "x"},  # c2 result missing (cancelled)
        {"role": "assistant", "content": "done"},
        {"role": "tool", "tool_call_id": "zz", "content": "stray"},
    ]
    out = _drop_orphans(msgs)
    assert [m["role"] for m in out] == ["user", "assistant"]


def test_context_trim_starts_with_user():
    msgs = [{"role": "user", "content": "x" * 1000}, {"role": "assistant", "content": "y" * 1000},
            {"role": "user", "content": "short"}, {"role": "assistant", "content": "ok"}]
    out = _trim(msgs, 500, 10)
    assert out[0]["role"] == "user" and out[0]["content"] == "short"


# ------------------------------------------------------------ orchestrator
@pytest.fixture
async def app_env(paths, secrets, groq, workspace):
    from fastapi.testclient import TestClient
    from core.app import create_app
    app = create_app(paths=paths, secrets=secrets, groq_transport=groq.transport, auth_token="tok", allowed_origins=["app://jarvis"], enable_voice=False)
    with TestClient(app) as c:
        c.headers["X-Jarvis-Token"] = "tok"
        c.put("/api/settings", json={"permissions": {"workspace_roots": [str(workspace)]}})
        yield app, c


async def _wait_task(app, events_q, timeout=10.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    seen = []
    while loop.time() < end:
        try:
            e = await asyncio.wait_for(events_q.get(), 0.5)
        except asyncio.TimeoutError:
            continue
        seen.append(e)
        if e["type"] == "task.finished":
            return seen
    raise AssertionError(f"task did not finish: {[e['type'] for e in seen]}")


def test_orchestrator_llm_tool_loop(app_env, groq, workspace):
    app, c = app_env
    (workspace / "notes.txt").write_text("GreenBot launch is Friday", encoding="utf-8")
    c.post("/api/providers", json={"label": "k", "api_key": "gsk_test_key_abcd", "quota_scope": "org"})
    groq.push(body=tool_completion([("list_directory", {"path": str(workspace)}), ("read_file", {"path": str(workspace / "notes.txt")})]))
    groq.push(body=text_completion("The launch is on Friday."))
    with c.websocket_connect("/ws?token=tok", headers={"origin": "app://jarvis"}) as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "chat.send", "text": "when is the GreenBot launch according to my notes file?"})
        events = []
        while True:
            e = ws.receive_json()
            events.append(e)
            if e["type"] == "task.finished":
                break
    types = [e["type"] for e in events]
    assert types.count("tool.started") == 2 and "assistant.delta" in types
    final = next(e for e in events if e["type"] == "assistant.message")
    assert final["content"] == "The launch is on Friday."
    fin = events[-1]
    assert fin["route"] == "llm" and fin["llm_calls"] == 2 and fin["tool_calls"] == 2
    # Second LLM request contained the tool results, wrapped as untrusted file content.
    second = json.loads(groq.requests[-1].content)
    tool_msgs = [m for m in second["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and "GreenBot launch is Friday" in tool_msgs[1]["content"]
    assert "<untrusted_content" in tool_msgs[1]["content"]
    # Follow-up keeps conversation context.
    cid = final["conversation_id"]
    groq.push(body=text_completion("Yes."))
    with c.websocket_connect("/ws?token=tok", headers={"origin": "app://jarvis"}) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "is that this week?", "conversation_id": cid})
        while ws.receive_json()["type"] != "task.finished":
            pass
    third = json.loads(groq.requests[-1].content)
    assert any("when is the GreenBot launch" in (m.get("content") or "") for m in third["messages"])


def test_direct_route_uses_no_llm(app_env, groq, monkeypatch):
    app, c = app_env
    launched = []
    from tools import apps
    monkeypatch.setattr(apps._INDEX, "resolve", lambda name: apps.AppMatch("Google Chrome", "chrome.exe", "exe"))
    monkeypatch.setattr(apps, "_launch", lambda m: launched.append(m.target))
    with c.websocket_connect("/ws?token=tok", headers={"origin": "app://jarvis"}) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "Open Chrome"})
        events = []
        while (e := ws.receive_json())["type"] != "task.finished":
            events.append(e)
    assert launched == ["chrome.exe"]
    assert e["route"] == "direct" and e["llm_calls"] == 0
    assert next(x for x in events if x["type"] == "assistant.message")["content"] == "Opening Google Chrome."
    assert groq.requests == []


def test_task_cancellation(app_env, groq):
    app, c = app_env
    c.post("/api/providers", json={"label": "k", "api_key": "gsk_test_key_abcd"})
    # Level 5 tools always wait for approval, which gives a deterministic point to cancel at.
    groq.push(body=tool_completion([("delete_file", {"path": "nothing.txt"})]))
    with c.websocket_connect("/ws?token=tok", headers={"origin": "app://jarvis"}) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "delete nothing.txt from my workspace please and then tell me"})
        task_id = None
        while True:
            e = ws.receive_json()
            if e["type"] == "task.started":
                task_id = e["task_id"]
            if e["type"] == "approval.request":
                ws.send_json({"type": "task.cancel", "task_id": task_id})
            if e["type"] in ("task.cancelled", "task.finished"):
                break
    assert e["type"] in ("task.cancelled", "task.finished")
    rows = c.get("/api/tasks").json()["history"]
    assert rows[0]["status"] == "cancelled"


def test_no_provider_gives_clear_message(app_env):
    app, c = app_env
    with c.websocket_connect("/ws?token=tok", headers={"origin": "app://jarvis"}) as ws:
        ws.receive_json()
        ws.send_json({"type": "chat.send", "text": "summarize my week and tell me what is pending"})
        while (e := ws.receive_json())["type"] != "assistant.message":
            pass
    assert "No Groq API key" in e["content"]
