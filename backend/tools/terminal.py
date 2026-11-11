"""Controlled subprocess execution.

- No shell: the model supplies an executable and an argv list; nothing is
  interpreted by cmd.exe/PowerShell. Shell interpreters are blocked outright.
- Working directory must be inside a workspace root.
- Output is capped; processes are tracked, time-limited and killed as a tree.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import Field

from agent.tool_registry import Assessment, ToolContext, ToolError, ToolInput, ToolRegistry, ToolResult
from core.secrets import redact
from tools import pathguard

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

BLOCKED_EXECUTABLES = {
    "cmd", "powershell", "pwsh", "powershell_ise", "bash", "sh", "wsl", "wscript", "cscript", "mshta", "regedit",
    "reg", "rundll32", "regsvr32", "bcdedit", "diskpart", "format", "shutdown", "schtasks", "sc", "net", "net1",
    "netsh", "takeown", "icacls", "cacls", "cipher", "vssadmin", "wmic", "certutil", "bitsadmin", "msiexec",
    "runas", "psexec", "sdelete", "attrib", "del", "erase", "rd", "rmdir", "mklink", "wevtutil", "fsutil", "setx",
}
RISKY = re.compile(r"\b(install|uninstall|add|remove|publish|push|deploy|reset|clean|prune|rm|delete|drop|force|upgrade|update|migrate)\b"
                   r"|(?:^|\s)(?:-f|--force|-rf)(?:\s|$)", re.I)
BATCH_META = re.compile(r'[&|<>^%!"\r\n]')


@dataclass
class ManagedProcess:
    id: str
    argv: list[str]
    cwd: str
    proc: asyncio.subprocess.Process
    started_at: float
    background: bool
    max_bytes: int
    lines: deque[str] = field(default_factory=deque)
    size: int = 0
    dropped: int = 0
    reader: asyncio.Task[None] | None = None
    finished_at: float | None = None

    def append(self, line: str) -> None:
        self.lines.append(line)
        self.size += len(line)
        while self.size > self.max_bytes and self.lines:
            self.size -= len(self.lines.popleft())
            self.dropped += 1

    def tail(self, n: int) -> str:
        out = list(self.lines)[-n:]
        prefix = f"[{self.dropped} earlier lines dropped]\n" if self.dropped else ""
        return prefix + "".join(out)

    def info(self) -> dict:
        return {"process_id": self.id, "pid": self.proc.pid, "command": " ".join(self.argv), "cwd": self.cwd,
                "running": self.proc.returncode is None, "exit_code": self.proc.returncode,
                "started_at": self.started_at, "background": self.background,
                "runtime_s": round((self.finished_at or time.time()) - self.started_at, 1)}


def kill_tree(pid: int) -> None:
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        import psutil
        try:
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.Error:
            pass


class ProcessManager:
    def __init__(self) -> None:
        self.processes: dict[str, ManagedProcess] = {}

    async def start(self, executable: str, args: list[str], cwd: Path, *, background: bool, max_bytes: int,
                    env_extra: dict[str, str] | None = None) -> ManagedProcess:
        env = {**os.environ, **(env_extra or {}), "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"}
        for secret_var in ("JARVIS_AUTH_TOKEN",):
            env.pop(secret_var, None)
        proc = await asyncio.create_subprocess_exec(
            executable, *args, cwd=str(cwd), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, env=env, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
        mp = ManagedProcess(uuid.uuid4().hex[:8], [executable, *args], str(cwd), proc, time.time(), background, max_bytes)
        mp.reader = asyncio.create_task(self._read(mp))
        self.processes[mp.id] = mp
        self._prune()
        return mp

    async def _read(self, mp: ManagedProcess) -> None:
        assert mp.proc.stdout
        try:
            while True:
                line = await mp.proc.stdout.readline()
                if not line:
                    break
                mp.append(redact(line.decode("utf-8", errors="replace")))
        finally:
            await mp.proc.wait()
            mp.finished_at = time.time()

    async def wait(self, mp: ManagedProcess, timeout: float) -> bool:
        try:
            await asyncio.wait_for(asyncio.shield(mp.reader), timeout)  # type: ignore[arg-type]
            return True
        except asyncio.TimeoutError:
            return False

    async def stop(self, mp: ManagedProcess) -> None:
        if mp.proc.returncode is None:
            await asyncio.to_thread(kill_tree, mp.proc.pid)
            try:
                await asyncio.wait_for(mp.proc.wait(), 5)
            except asyncio.TimeoutError:
                pass

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(mp) for mp in list(self.processes.values())), return_exceptions=True)

    def _prune(self) -> None:
        finished = sorted((m for m in self.processes.values() if m.proc.returncode is not None), key=lambda m: m.started_at)
        for m in finished[:-20]:
            self.processes.pop(m.id, None)

    def list(self) -> list[dict]:
        return [m.info() for m in self.processes.values()]


# ------------------------------------------------------------------ inputs
class RunCommandIn(ToolInput):
    executable: str = Field(..., min_length=1, max_length=260, description="Program to run, e.g. 'git', 'npm', 'python'. No shells.")
    args: list[str] = Field(default_factory=list, max_length=64, description="Arguments, one per item")
    cwd: Optional[str] = Field(None, description="Working directory inside the workspace")
    timeout_s: int = Field(60, ge=1, le=1800)


class RunProjectIn(ToolInput):
    executable: str = Field(..., min_length=1, max_length=260, description="e.g. 'npm'")
    args: list[str] = Field(default_factory=list, max_length=64, description="e.g. ['run', 'dev']")
    cwd: str = Field(..., description="Project directory inside the workspace")


class ProcessRefIn(ToolInput):
    process_id: Optional[str] = Field(None, description="ID of a process started by Jarvis")
    pid: Optional[int] = Field(None, ge=1, description="OS process id (for processes not started by Jarvis)")


class ProcessOutputIn(ToolInput):
    process_id: str
    tail_lines: int = Field(100, ge=1, le=2000)


def _exe_stem(executable: str) -> str:
    return Path(executable).name.lower().removesuffix(".exe").removesuffix(".cmd").removesuffix(".bat")


def _resolve_exe(executable: str) -> str:
    stem = _exe_stem(executable)
    if stem in BLOCKED_EXECUTABLES:
        raise ToolError(f"'{executable}' is blocked: shells and system administration tools cannot be run by Jarvis.", code="blocked")
    found = shutil.which(executable)
    if not found:
        raise ToolError(f"'{executable}' was not found on PATH. Shell built-ins (dir, echo, cd) are not supported; use the file tools instead.", code="not_found")
    if _exe_stem(found) in BLOCKED_EXECUTABLES:
        raise ToolError(f"'{executable}' resolves to a blocked program.", code="blocked")
    return found


def _check_batch_args(exe_path: str, args: list[str]) -> None:
    # .cmd/.bat files are run through cmd.exe by Windows; refuse metacharacters
    # that cmd would interpret (prevents argument injection).
    if exe_path.lower().endswith((".cmd", ".bat")) and any(BATCH_META.search(a) for a in args):
        raise ToolError("Arguments contain characters that are unsafe for batch-file programs (& | < > ^ % ! \").", code="blocked")


def _assess(args: RunCommandIn | RunProjectIn, ctx: ToolContext) -> Assessment:
    stem = _exe_stem(args.executable)
    if stem in BLOCKED_EXECUTABLES:
        raise ToolError(f"'{args.executable}' is blocked: shells and system administration tools cannot be run by Jarvis.", code="blocked")
    cmdline = " ".join([stem, *args.args]).lower()
    allow = any(cmdline == entry.lower() or cmdline.startswith(entry.lower() + " ") for entry in ctx.settings.permissions.command_allowlist)
    # Output/redirect-style options can turn a read-only command (e.g. `git log
    # --output=f`) into a file write, so they never run without approval.
    writes = any(re.match(r"^(--?o(utput)?|--out-file|--log-file)(=|$)", a, re.I) for a in args.args)
    risky = bool(RISKY.search(" ".join(args.args))) or writes
    reason = "Command looks like it installs, removes or publishes something." if risky else None
    return Assessment(4, auto_ok=allow and not risky and isinstance(args, RunCommandIn), reason=reason)


def _describe(args: RunCommandIn | RunProjectIn) -> str:
    where = f" in {args.cwd}" if args.cwd else ""
    return f"Run `{' '.join([args.executable, *args.args])}`{where}"


def register(reg: ToolRegistry) -> None:
    def _cwd(ctx: ToolContext, raw: str | None) -> Path:
        roots = pathguard.workspace_roots(ctx.settings)
        p = pathguard.resolve(raw, ctx.settings, ctx.services.paths, must_exist=True, label="cwd") if raw else (roots[0] if roots else None)
        if p is None or not p.is_dir():
            raise ToolError("Working directory must be an existing folder inside the workspace.", code="invalid_path")
        return p

    @reg.tool("run_command", "Run a program (no shell) and wait for it to finish. Returns exit code and output. "
              "Use for git, npm, python, pytest, etc. Long-running servers should use run_project.",
              input_model=RunCommandIn, level=4, timeout_s=1900, category="terminal", assess=_assess, describe=_describe)
    async def run_command(args: RunCommandIn, ctx: ToolContext) -> ToolResult:
        exe = _resolve_exe(args.executable)
        _check_batch_args(exe, args.args)
        cwd = _cwd(ctx, args.cwd)
        auto = ctx.settings.automation
        pm: ProcessManager = ctx.services.processes
        mp = await pm.start(exe, args.args, cwd, background=False, max_bytes=auto.max_output_kb * 1024)
        timeout = min(args.timeout_s, auto.command_timeout_s)
        try:
            finished = await pm.wait(mp, timeout)
        except asyncio.CancelledError:
            await pm.stop(mp)
            raise
        if not finished:
            await pm.stop(mp)
            return ToolResult(False, f"Command timed out after {timeout}s and was stopped.",
                              {"output": mp.tail(80), **mp.info()}, error="timeout", code="timeout", untrusted=True, source="process")
        code = mp.proc.returncode
        return ToolResult(code == 0, f"`{' '.join([_exe_stem(exe), *args.args])}` exited with code {code}.",
                          {"exit_code": code, "output": mp.tail(400), "cwd": str(cwd)},
                          error=None if code == 0 else f"exit code {code}", untrusted=True, source="process")

    @reg.tool("run_project", "Start a long-running process in the background (e.g. `npm run dev`). "
              "Returns a process_id; use get_process_output to read its terminal output.",
              input_model=RunProjectIn, level=4, timeout_s=30, category="terminal", assess=_assess, describe=_describe)
    async def run_project(args: RunProjectIn, ctx: ToolContext) -> ToolResult:
        exe = _resolve_exe(args.executable)
        _check_batch_args(exe, args.args)
        cwd = _cwd(ctx, args.cwd)
        pm: ProcessManager = ctx.services.processes
        mp = await pm.start(exe, args.args, cwd, background=True, max_bytes=ctx.settings.automation.max_output_kb * 1024)
        await pm.wait(mp, 4)  # capture early output / immediate failures
        return ToolResult.success(f"Started `{' '.join([_exe_stem(exe), *args.args])}` (process {mp.id}).",
                                  {**mp.info(), "initial_output": mp.tail(60)}, untrusted=True, source="process")

    @reg.tool("get_process_output", "Read recent terminal output of a process started by Jarvis.",
              input_model=ProcessOutputIn, level=0, idempotent=True, category="terminal")
    async def get_process_output(args: ProcessOutputIn, ctx: ToolContext) -> ToolResult:
        mp = ctx.services.processes.processes.get(args.process_id)
        if not mp:
            raise ToolError(f"No Jarvis-managed process {args.process_id}. Known: {', '.join(ctx.services.processes.processes) or 'none'}.", code="not_found")
        return ToolResult.success(f"Output of process {mp.id} ({'running' if mp.proc.returncode is None else f'exited {mp.proc.returncode}'}).",
                                  {**mp.info(), "output": mp.tail(args.tail_lines)}, untrusted=True, source="process")

    @reg.tool("inspect_process", "Inspect a process: status, CPU, memory, command line. Without arguments lists Jarvis-managed processes.",
              input_model=ProcessRefIn, level=0, idempotent=True, category="terminal")
    async def inspect_process(args: ProcessRefIn, ctx: ToolContext) -> ToolResult:
        import psutil

        pm: ProcessManager = ctx.services.processes
        if not args.process_id and not args.pid:
            return ToolResult.success(f"{len(pm.processes)} Jarvis-managed process(es).", {"processes": pm.list()})
        pid = args.pid
        extra: dict = {}
        if args.process_id:
            mp = pm.processes.get(args.process_id)
            if not mp:
                raise ToolError(f"No Jarvis-managed process {args.process_id}.", code="not_found")
            pid, extra = mp.proc.pid, mp.info()

        def _inspect() -> dict:
            p = psutil.Process(pid)
            with p.oneshot():
                return {"pid": p.pid, "name": p.name(), "status": p.status(), "cpu_percent": p.cpu_percent(interval=0.2),
                        "memory_mb": round(p.memory_info().rss / 1048576, 1), "threads": p.num_threads(),
                        "cmdline": redact(" ".join(p.cmdline()))[:1000], "started": time.ctime(p.create_time()),
                        "children": [c.pid for c in p.children(recursive=True)][:50]}
        try:
            info = await asyncio.to_thread(_inspect)
        except psutil.NoSuchProcess:
            if extra:
                return ToolResult.success("Process has exited.", extra)
            raise ToolError(f"No process with PID {pid}.", code="not_found")
        except psutil.AccessDenied:
            raise ToolError(f"Access denied to process {pid}.", code="denied")
        return ToolResult.success(f"Process {info['name']} ({pid}) is {info['status']}.", {**extra, **info})

    def _stop_assess(args: ProcessRefIn, ctx: ToolContext) -> Assessment:
        if args.process_id and args.process_id in ctx.services.processes.processes:
            return Assessment(1, reason="Process was started by Jarvis.")
        return Assessment(4, reason="Stopping a process Jarvis did not start.")

    @reg.tool("stop_process", "Stop a process (and its children). Prefer process_id for processes Jarvis started.",
              input_model=ProcessRefIn, level=1, timeout_s=20, category="terminal", assess=_stop_assess,
              describe=lambda a: f"Stop process {a.process_id or a.pid}")
    async def stop_process(args: ProcessRefIn, ctx: ToolContext) -> ToolResult:
        import psutil

        pm: ProcessManager = ctx.services.processes
        if args.process_id:
            mp = pm.processes.get(args.process_id)
            if not mp:
                raise ToolError(f"No Jarvis-managed process {args.process_id}.", code="not_found")
            await pm.stop(mp)
            return ToolResult.success(f"Stopped process {mp.id}.", mp.info())
        if not args.pid:
            raise ToolError("Provide process_id or pid.", code="invalid_arguments")
        try:
            name = psutil.Process(args.pid).name().lower()
        except psutil.NoSuchProcess:
            raise ToolError(f"No process with PID {args.pid}.", code="not_found")
        from tools.apps import PROTECTED_PROCESSES
        if name in PROTECTED_PROCESSES or args.pid in (os.getpid(), os.getppid()):
            raise ToolError(f"{name} is protected.", code="protected")
        await asyncio.to_thread(kill_tree, args.pid)
        return ToolResult.success(f"Stopped {name} (PID {args.pid}).", {"pid": args.pid, "name": name})
