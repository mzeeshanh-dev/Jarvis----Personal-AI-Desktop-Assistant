"""Application tools: launch, list, close apps and open URLs (Windows)."""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator

from agent.tool_registry import ToolContext, ToolError, ToolInput, ToolRegistry, ToolResult

CREATE_NO_WINDOW = 0x08000000


def _env(var: str, rest: str) -> str:
    return str(Path(os.environ.get(var, ""), rest))


# Canonical name -> (display name, executable candidates / URIs)
KNOWN_APPS: dict[str, tuple[str, list[str]]] = {
    "chrome": ("Google Chrome", [_env("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
                                 _env("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
                                 _env("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"), "chrome.exe"]),
    "edge": ("Microsoft Edge", [_env("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
                                _env("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"), "msedge.exe"]),
    "firefox": ("Firefox", [_env("ProgramFiles", r"Mozilla Firefox\firefox.exe"), "firefox.exe"]),
    "vscode": ("Visual Studio Code", [_env("LOCALAPPDATA", r"Programs\Microsoft VS Code\Code.exe"),
                                      _env("ProgramFiles", r"Microsoft VS Code\Code.exe"), "code.cmd"]),
    "notepad": ("Notepad", ["notepad.exe"]),
    "calculator": ("Calculator", ["calc.exe"]),
    "paint": ("Paint", ["mspaint.exe"]),
    "explorer": ("File Explorer", ["explorer.exe"]),
    "terminal": ("Windows Terminal", ["wt.exe"]),
    "cmd": ("Command Prompt", ["cmd.exe"]),
    "powershell": ("PowerShell", ["powershell.exe"]),
    "task manager": ("Task Manager", ["taskmgr.exe"]),
    "settings": ("Settings", ["ms-settings:"]),
    "control panel": ("Control Panel", ["control.exe"]),
    "snipping tool": ("Snipping Tool", ["ms-screenclip:"]),
    "word": ("Microsoft Word", ["winword.exe"]),
    "excel": ("Microsoft Excel", ["excel.exe"]),
    "powerpoint": ("Microsoft PowerPoint", ["powerpnt.exe"]),
    "outlook": ("Outlook", ["outlook.exe"]),
    "spotify": ("Spotify", [_env("APPDATA", r"Spotify\Spotify.exe")]),
}

ALIASES = {
    "google chrome": "chrome", "chrome browser": "chrome", "microsoft edge": "edge", "edge browser": "edge",
    "mozilla firefox": "firefox", "vs code": "vscode", "visual studio code": "vscode", "code": "vscode",
    "calc": "calculator", "file explorer": "explorer", "files": "explorer", "windows explorer": "explorer",
    "windows terminal": "terminal", "command prompt": "cmd", "taskmgr": "task manager",
    "ms word": "word", "microsoft word": "word", "ms excel": "excel", "microsoft excel": "excel",
}

PROTECTED_PROCESSES = {"system", "idle", "csrss.exe", "wininit.exe", "winlogon.exe", "lsass.exe", "services.exe",
                       "smss.exe", "svchost.exe", "dwm.exe", "fontdrvhost.exe", "registry", "memory compression",
                       "jarvis.exe", "jarvis-backend.exe", "electron.exe", "msmpeng.exe", "securityhealthservice.exe"}


@dataclass
class AppMatch:
    display: str
    target: str
    kind: str  # exe | lnk | uri | uwp


class AppIndex:
    """Start Menu shortcut index + Store apps, built lazily once per session."""

    def __init__(self) -> None:
        self._lnk: dict[str, str] | None = None
        self._uwp: dict[str, str] | None = None

    def shortcuts(self) -> dict[str, str]:
        if self._lnk is None:
            idx: dict[str, str] = {}
            for base in (os.environ.get("ProgramData", ""), os.environ.get("APPDATA", "")):
                root = Path(base, r"Microsoft\Windows\Start Menu\Programs")
                if root.exists():
                    for lnk in root.rglob("*.lnk"):
                        name = lnk.stem.lower()
                        if "uninstall" in name or "readme" in name or "help" == name:
                            continue
                        idx.setdefault(name, str(lnk))
            self._lnk = idx
        return self._lnk

    def store_apps(self) -> dict[str, str]:
        if self._uwp is None:
            self._uwp = {}
            if sys.platform == "win32":
                try:
                    out = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                         "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                        capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW)
                    data = json.loads(out.stdout or "[]")
                    for item in data if isinstance(data, list) else [data]:
                        if item.get("Name") and item.get("AppID"):
                            self._uwp.setdefault(item["Name"].lower(), item["AppID"])
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
        return self._uwp

    def resolve(self, name: str) -> AppMatch | None:
        q = re.sub(r"\s+", " ", name.lower().strip())
        canonical = ALIASES.get(q, q)
        if canonical in KNOWN_APPS:
            display, candidates = KNOWN_APPS[canonical]
            for c in candidates:
                if c.endswith(":"):
                    return AppMatch(display, c, "uri")
                if os.path.isabs(c) and os.path.exists(c):
                    return AppMatch(display, c, "exe")
                if not os.path.isabs(c) and (found := shutil.which(c)):
                    return AppMatch(display, found, "exe")
        for table, kind in ((self.shortcuts(), "lnk"), (self.store_apps(), "uwp")):
            if not table:
                continue
            if q in table:
                return AppMatch(_title(q), table[q], kind)
            starts = sorted((k for k in table if k.startswith(q) or q in k.split()), key=len)
            if starts:
                return AppMatch(_title(starts[0]), table[starts[0]], kind)
            close = difflib.get_close_matches(q, list(table), n=1, cutoff=0.8)
            if close:
                return AppMatch(_title(close[0]), table[close[0]], kind)
        return None


def _title(s: str) -> str:
    return " ".join(w if w.isupper() else w.capitalize() for w in s.split())


def _launch(m: AppMatch) -> None:
    if m.kind == "uwp":
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{m.target}"], creationflags=CREATE_NO_WINDOW)
    elif m.kind == "exe" and m.target.lower().endswith(".cmd"):
        subprocess.Popen([m.target], creationflags=CREATE_NO_WINDOW, close_fds=True)
    else:
        os.startfile(m.target)  # type: ignore[attr-defined]


def visible_windows() -> dict[int, list[str]]:
    """PID -> titles of visible top-level windows."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    result: dict[int, list[str]] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):  # noqa: ANN001
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # GW_OWNER
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                result.setdefault(pid.value, []).append(buf.value)
        return True

    user32.EnumWindows(cb, 0)
    return result


class OpenAppIn(ToolInput):
    name: str = Field(..., min_length=1, max_length=80, description="Application name, e.g. 'chrome', 'VS Code', 'Spotify'")


class CloseAppIn(ToolInput):
    name: str = Field(..., min_length=1, max_length=80, description="Application or process name, e.g. 'notepad' or 'chrome.exe'")
    force: bool = Field(False, description="Force-kill instead of asking the app to close (may lose unsaved work)")


class ListAppsIn(ToolInput):
    include_background: bool = Field(False, description="Include processes without visible windows")


class OpenUrlIn(ToolInput):
    url: str = Field(..., max_length=4000)

    @field_validator("url")
    @classmethod
    def _check(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r"^[a-z][a-z0-9+.-]*:", v, re.I):
            v = "https://" + v
        p = urlparse(v)
        if p.scheme not in ("http", "https", "mailto"):
            raise ValueError("only http, https and mailto URLs are allowed")
        if p.scheme != "mailto" and not p.netloc:
            raise ValueError("URL has no host")
        return v


_INDEX = AppIndex()


def register(reg: ToolRegistry) -> None:
    @reg.tool("open_application", "Launch a desktop application by name (installed programs, Start Menu and Store apps).",
              input_model=OpenAppIn, level=1, timeout_s=20, category="apps")
    async def open_application(args: OpenAppIn, ctx: ToolContext) -> ToolResult:
        match = await asyncio.to_thread(_INDEX.resolve, args.name)
        if not match:
            raise ToolError(f"I couldn't find an application called “{args.name}”.", code="not_found")
        await asyncio.to_thread(_launch, match)
        return ToolResult.success(f"Opening {match.display}.", {"app": match.display, "kind": match.kind})

    @reg.tool("list_running_applications", "List running applications (processes with visible windows) and their memory use.",
              input_model=ListAppsIn, level=0, idempotent=True, category="apps")
    async def list_running_applications(args: ListAppsIn, ctx: ToolContext) -> ToolResult:
        import psutil

        def _list() -> list[dict]:
            windows = visible_windows()
            apps: dict[str, dict] = {}
            for proc in psutil.process_iter(["pid", "name", "memory_info"]):
                info = proc.info
                name = (info.get("name") or "").lower()
                titles = windows.get(info["pid"], [])
                if not titles and not args.include_background:
                    continue
                a = apps.setdefault(name, {"process": info.get("name"), "pids": [], "windows": [], "memory_mb": 0.0})
                a["pids"].append(info["pid"])
                a["windows"].extend(titles[:3])
                mem = info.get("memory_info")
                a["memory_mb"] += round((mem.rss if mem else 0) / 1048576, 1)
            return sorted(apps.values(), key=lambda a: -a["memory_mb"])[:80]
        apps = await asyncio.to_thread(_list)
        return ToolResult.success(f"{len(apps)} applications running.", {"applications": apps})

    @reg.tool("close_application", "Close a running application by name. Asks the app to close gracefully unless force=true.",
              input_model=CloseAppIn, level=3, timeout_s=20, category="apps",
              describe=lambda a: f"{'Force-kill' if a.force else 'Close'} {a.name}")
    async def close_application(args: CloseAppIn, ctx: ToolContext) -> ToolResult:
        import psutil

        q = args.name.lower().strip()
        canonical = ALIASES.get(q, q)
        exe_names = {q if q.endswith(".exe") else f"{q}.exe"}
        if canonical in KNOWN_APPS:
            exe_names |= {Path(c).name.lower() for c in KNOWN_APPS[canonical][1] if c.lower().endswith(".exe")}
        if exe_names & PROTECTED_PROCESSES:
            raise ToolError(f"{args.name} is a protected system process.", code="protected")
        procs = [p for p in psutil.process_iter(["pid", "name"]) if (p.info.get("name") or "").lower() in exe_names]
        if not procs:
            raise ToolError(f"No running application matches “{args.name}”.", code="not_found")
        own = os.getpid()
        procs = [p for p in procs if p.pid != own]

        def _close() -> int:
            names = {p.info["name"] for p in procs}
            for n in names:
                cmd = ["taskkill", "/IM", n] + (["/F", "/T"] if args.force else [])
                subprocess.run(cmd, capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
            return len(procs)
        n = await asyncio.to_thread(_close)
        return ToolResult.success(f"Asked {args.name} to close ({n} process{'es' if n != 1 else ''}).", {"processes": n})

    @reg.tool("open_url", "Open a web URL in the user's default browser.", input_model=OpenUrlIn, level=1, category="apps")
    async def open_url(args: OpenUrlIn, ctx: ToolContext) -> ToolResult:
        await asyncio.to_thread(os.startfile, args.url)  # type: ignore[attr-defined]
        host = urlparse(args.url).netloc or args.url
        return ToolResult.success(f"Opening {host}.", {"url": args.url})
