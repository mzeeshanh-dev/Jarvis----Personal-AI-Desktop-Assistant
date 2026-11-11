"""Real browser automation against a local page, using the installed Edge
(headless). Skipped when Edge is not installed."""
from __future__ import annotations

import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from agent.executor import Executor, ToolCall
from agent.permissions import ApprovalBroker
from agent.tool_registry import ToolContext
from tools import build_registry
from tools.browser import BrowserManager

EDGE = Path(os.environ.get("ProgramFiles(x86)", ""), r"Microsoft\Edge\Application\msedge.exe")
pytestmark = pytest.mark.skipif(not EDGE.exists(), reason="Microsoft Edge not installed")

PAGE = b"""<!doctype html><html><head><title>Jarvis test page</title></head><body>
<main><h1>GreenBot status</h1><p>Two tasks pending: dashboard review, sensor report.</p>
<a href="/next">Next page</a>
<form onsubmit="event.preventDefault(); document.getElementById('out').textContent = 'Hello ' + document.getElementById('n').value;">
<label for="n">Your name</label><input id="n"><label for="pw">Password</label><input id="pw" type="password">
<button type="submit">Greet</button></form><div id="out"></div></main></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGE if self.path == "/" else b"<title>Next</title><main>Second page</main>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class FakePerf:
    def record(self, *_a): pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


async def test_browser_tools_end_to_end(server, db, events, paths, settings):
    settings.browser.headless = True
    settings.permissions.level3_policy = "auto"

    class Svc:
        pass
    svc = Svc()
    svc.db, svc.events, svc.paths, svc.perf = db, events, paths, FakePerf()
    svc.approvals = ApprovalBroker(events)
    svc.browser = BrowserManager(paths, lambda: settings)
    ctx = ToolContext("t", None, settings, svc)
    ex = Executor(build_registry())
    # Page content is untrusted, so later level-3 actions ask: auto-approve here.
    q = events.subscribe()

    async def approve():
        while True:
            e = await q.get()
            if e["type"] == "approval.request":
                svc.approvals.respond(e["id"], True)
    approver = asyncio.create_task(approve())
    try:
        r = await ex.run(ToolCall("1", "browser_navigate", {"url": server}), ctx)
        assert r.ok, r.error
        r = await ex.run(ToolCall("2", "browser_read_page", {}), ctx)
        assert r.ok and "Two tasks pending" in r.data["text"] and r.untrusted
        r = await ex.run(ToolCall("3", "browser_extract_content", {"kind": "links"}), ctx)
        assert r.ok and r.data["items"][0]["text"] == "Next page"
        r = await ex.run(ToolCall("4", "browser_fill", {"label": "Password", "value": "x"}), ctx)
        assert not r.ok and r.code == "blocked"  # never fills passwords
        r = await ex.run(ToolCall("5", "browser_fill", {"label": "Your name", "value": "Zeeshan"}), ctx)
        assert r.ok, r.error
        r = await ex.run(ToolCall("6", "browser_click", {"role": "button", "name": "Greet"}), ctx)
        assert r.ok, r.error
        page = svc.browser.page
        assert await page.text_content("#out") == "Hello Zeeshan"
        r = await ex.run(ToolCall("7", "browser_screenshot", {}), ctx)
        assert r.ok and Path(r.data["path"]).stat().st_size > 1000
        r = await ex.run(ToolCall("8", "browser_click", {"text": "Next page"}), ctx)
        assert r.ok and r.data["url"].endswith("/next")
        r = await ex.run(ToolCall("9", "browser_close", {}), ctx)
        assert r.ok and not svc.browser.running
    finally:
        approver.cancel()
        await svc.browser.close()
