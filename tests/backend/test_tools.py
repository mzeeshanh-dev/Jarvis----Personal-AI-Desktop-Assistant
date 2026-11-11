"""Filesystem restrictions, terminal safety (blocking, timeouts, cancellation),
app tools validation, browser URL validation."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent.executor import Executor, ToolCall
from agent.permissions import ApprovalBroker
from agent.tool_registry import ToolContext, ToolError
from tools import build_registry, pathguard
from tools.terminal import ProcessManager, RunCommandIn, _assess


class FakePerf:
    def record(self, *_a): pass


class Svc:
    def __init__(self, db, events, paths):
        self.db, self.events, self.paths = db, events, paths
        self.approvals = ApprovalBroker(events)
        self.perf = FakePerf()
        self.processes = ProcessManager()


@pytest.fixture
def ctx(db, events, paths, settings):
    settings.permissions.level3_policy = "auto"
    return ToolContext("t", None, settings, Svc(db, events, paths))


@pytest.fixture
def ex():
    return Executor(build_registry())


# --------------------------------------------------------------- pathguard
def test_path_inside_workspace_allowed(settings, paths, workspace):
    (workspace / "a.txt").write_text("x")
    assert pathguard.resolve(str(workspace / "a.txt"), settings, paths) == (workspace / "a.txt").resolve()
    assert pathguard.resolve("a.txt", settings, paths) == (workspace / "a.txt").resolve()  # relative -> first root


@pytest.mark.parametrize("raw", ["..\\..\\outside.txt", "C:\\Windows\\System32\\drivers\\etc\\hosts", "\\\\server\\share\\x"])
def test_path_escape_denied(settings, paths, workspace, raw):
    with pytest.raises(ToolError):
        pathguard.resolve(raw if not raw.startswith("..") else str(workspace / raw), settings, paths)


@pytest.mark.parametrize("name", [".env", "id_rsa", "server.pem", "vault.kdbx", ".git-credentials"])
def test_sensitive_files_denied(settings, paths, workspace, name):
    (workspace / name).write_text("secret")
    with pytest.raises(ToolError):
        pathguard.resolve(str(workspace / name), settings, paths)


def test_protected_root_denied_even_if_configured(settings, paths):
    import os
    settings.permissions.workspace_roots = [os.environ["APPDATA"]]
    with pytest.raises(ToolError, match="protected"):
        pathguard.resolve(str(Path(os.environ["APPDATA"]) / "x.txt"), settings, paths)


# -------------------------------------------------------------- filesystem
async def test_file_tools_roundtrip(ex, ctx, workspace, events):
    # After read_file returns untrusted content, later level-3 tools ask for approval.
    approver = _approve_all(events, ctx.services.approvals)
    f = workspace / "sub" / "note.md"
    r = await ex.run(ToolCall("1", "create_file", {"path": str(f), "content": "hello world"}), ctx)
    assert r.ok and f.read_text() == "hello world"
    r = await ex.run(ToolCall("2", "create_file", {"path": str(f), "content": "x"}), ctx)
    assert not r.ok and r.code == "exists"
    r = await ex.run(ToolCall("3", "edit_file", {"path": str(f), "find": "world", "replace": "Jarvis"}), ctx)
    assert r.ok and f.read_text() == "hello Jarvis"
    r = await ex.run(ToolCall("4", "read_file", {"path": str(f)}), ctx)
    assert r.ok and r.data["text"] == "hello Jarvis" and r.untrusted
    r = await ex.run(ToolCall("5", "search_files", {"query": "note"}), ctx)
    assert r.ok and r.data["results"][0]["name"] == "note.md"
    r = await ex.run(ToolCall("6", "search_files", {"query": "jarvis", "search_contents": True}), ctx)
    assert r.ok and r.data["results"][0]["matched"] == "content"
    r = await ex.run(ToolCall("7", "copy_file", {"source": str(f), "destination": str(workspace / "copy.md")}), ctx)
    assert r.ok and (workspace / "copy.md").exists()
    r = await ex.run(ToolCall("8", "move_file", {"source": str(workspace / "copy.md"), "destination": str(workspace / "moved.md")}), ctx)
    assert r.ok and (workspace / "moved.md").exists() and not (workspace / "copy.md").exists()
    r = await ex.run(ToolCall("9", "list_directory", {"path": str(workspace)}), ctx)
    assert {e["name"] for e in r.data["entries"]} == {"sub", "moved.md"}
    assert ctx.tainted
    approver.cancel()


async def test_read_pdf(ex, ctx, workspace):
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(200, 200)
    p = workspace / "doc.pdf"
    with open(p, "wb") as fh:
        w.write(fh)
    r = await ex.run(ToolCall("1", "read_file", {"path": str(p)}), ctx)
    assert r.ok and r.data["kind"].startswith("pdf (1 pages)")


async def test_file_tool_outside_workspace_blocked(ex, ctx, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    r = await ex.run(ToolCall("1", "read_file", {"path": str(outside)}), ctx)
    assert not r.ok and r.code == "path_denied"


async def test_delete_requires_approval_and_uses_recycle_bin(ex, ctx, workspace, events):
    victim = workspace / "trash-me.txt"
    victim.write_text("bye")
    q = events.subscribe()
    task = asyncio.create_task(ex.run(ToolCall("1", "delete_file", {"path": str(victim)}), ctx))
    while (e := await q.get())["type"] != "approval.request":
        pass
    assert e["level"] == 5
    ctx.services.approvals.respond(e["id"], True)
    r = await task
    assert r.ok and not victim.exists()


# ---------------------------------------------------------------- terminal
@pytest.mark.parametrize("exe", ["cmd", "powershell.exe", "pwsh", "reg", "diskpart", "C:\\Windows\\System32\\cmd.exe"])
async def test_shells_blocked(ex, ctx, exe):
    r = await ex.run(ToolCall("1", "run_command", {"executable": exe, "args": ["/c", "echo hi"]}), ctx)
    assert not r.ok and r.code == "blocked"


def test_allowlist_assessment(ctx):
    assert _assess(RunCommandIn(executable="git", args=["status"]), ctx).auto_ok
    assert not _assess(RunCommandIn(executable="git", args=["push", "--force"]), ctx).auto_ok
    assert not _assess(RunCommandIn(executable="npm", args=["install", "left-pad"]), ctx).auto_ok
    assert not _assess(RunCommandIn(executable="python", args=["evil.py"]), ctx).auto_ok
    # Allowlisted prefix + output-writing flag must still ask.
    assert not _assess(RunCommandIn(executable="git", args=["log", "--output=C:/x.txt"]), ctx).auto_ok
    assert _assess(RunCommandIn(executable="git", args=["log", "--oneline", "-5"]), ctx).auto_ok


def _approve_all(events, approvals) -> asyncio.Task:
    q = events.subscribe()  # subscribe before the tool runs

    async def loop():
        while True:
            e = await q.get()
            if e["type"] == "approval.request":
                approvals.respond(e["id"], True)
    return asyncio.create_task(loop())


async def test_run_command_output_and_exit_code(ex, ctx, workspace, events):
    approver = _approve_all(events, ctx.services.approvals)
    r = await ex.run(ToolCall("1", "run_command", {"executable": sys.executable, "args": ["-c", "print('hi from jarvis')"], "cwd": str(workspace)}), ctx)
    assert r.ok and "hi from jarvis" in r.data["output"] and r.data["exit_code"] == 0
    r = await ex.run(ToolCall("2", "run_command", {"executable": sys.executable, "args": ["-c", "import sys; sys.exit(3)"], "cwd": str(workspace)}), ctx)
    assert not r.ok and r.data["exit_code"] == 3
    approver.cancel()


async def test_run_command_timeout_kills(ex, ctx, workspace, events):
    approver = _approve_all(events, ctx.services.approvals)
    r = await ex.run(ToolCall("1", "run_command", {"executable": sys.executable, "args": ["-c", "import time; print('start', flush=True); time.sleep(30)"], "cwd": str(workspace), "timeout_s": 1}), ctx)
    assert not r.ok and r.code == "timeout"
    mp = next(iter(ctx.services.processes.processes.values()))
    assert mp.proc.returncode is not None
    approver.cancel()


async def test_run_command_cancellation_kills(ex, ctx, workspace, events):
    approver = _approve_all(events, ctx.services.approvals)
    task = asyncio.create_task(ex.run(ToolCall("1", "run_command", {"executable": sys.executable, "args": ["-c", "import time; time.sleep(30)"], "cwd": str(workspace)}), ctx))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if ctx.services.processes.processes:
            break
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    mp = next(iter(ctx.services.processes.processes.values()))
    await asyncio.wait_for(mp.proc.wait(), 5)
    assert mp.proc.returncode is not None
    approver.cancel()


async def test_run_project_and_output(ex, ctx, workspace, events):
    approver = _approve_all(events, ctx.services.approvals)
    r = await ex.run(ToolCall("1", "run_project", {"executable": sys.executable, "args": ["-c", "import time\nfor i in range(100):\n print('tick', i, flush=True); time.sleep(0.1)"], "cwd": str(workspace)}), ctx)
    assert r.ok
    pid = r.data["process_id"]
    r = await ex.run(ToolCall("2", "get_process_output", {"process_id": pid}), ctx)
    assert r.ok and "tick" in r.data["output"]
    r = await ex.run(ToolCall("3", "stop_process", {"process_id": pid}), ctx)  # level 1 for own processes
    assert r.ok
    approver.cancel()


async def test_cwd_outside_workspace_blocked(ex, ctx, events):
    approver = _approve_all(events, ctx.services.approvals)
    r = await ex.run(ToolCall("1", "run_command", {"executable": sys.executable, "args": ["-V"], "cwd": "C:\\Windows"}), ctx)
    assert not r.ok and r.code == "path_denied"
    approver.cancel()


# -------------------------------------------------------------- apps / web
async def test_open_url_rejects_dangerous_schemes(ex, ctx):
    for url in ["javascript:alert(1)", "file:///C:/Windows/win.ini", "ms-settings:"]:
        r = await ex.run(ToolCall("1", "open_url", {"url": url}), ctx)
        assert not r.ok and r.code == "invalid_arguments"


async def test_close_protected_process_refused(ex, ctx):
    r = await ex.run(ToolCall("1", "close_application", {"name": "lsass"}), ctx)
    assert not r.ok and r.code == "protected"


async def test_list_running_applications(ex, ctx):
    r = await ex.run(ToolCall("1", "list_running_applications", {}), ctx)
    assert r.ok and isinstance(r.data["applications"], list)


def test_app_index_resolves_builtin():
    from tools.apps import AppIndex
    m = AppIndex().resolve("notepad")
    assert m and m.target.lower().endswith("notepad.exe")


def test_browser_url_validation():
    from pydantic import ValidationError
    from tools.browser import NavigateIn
    assert NavigateIn(url="example.com").url == "https://example.com"
    with pytest.raises(ValidationError):
        NavigateIn(url="file:///etc/passwd")
