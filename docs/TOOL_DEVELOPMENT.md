# Tool development

Code: `backend/agent/tool_registry.py`, `backend/tools/*.py`, `backend/tools/__init__.py`.

## Concepts

| Type | Purpose |
|---|---|
| `ToolInput` | Pydantic base for argument models: `extra="forbid"` (unknown fields rejected), `str_strip_whitespace=True`. Its JSON schema, with `title` keys stripped, is what the LLM sees |
| `Tool` | `name, description, input_model, level (0–5), handler, timeout_s, idempotent, assess, describe, category` |
| `ToolRegistry.tool(...)` | Decorator that registers an async handler. Raises on a duplicate name or a level outside 0–5 |
| `ToolRegistry.register(tool, available=fn)` | Lower-level registration with an optional availability check; unavailable tools are hidden from the LLM and refused by the executor |
| `ToolContext` | `task_id, conversation_id, settings, services, tainted, tainted_by` |
| `ToolResult.success(summary, data, untrusted=False, source=None)` / `ToolResult.failure(error, code)` | Return values. `summary` is shown in the UI and the audit log; `data` goes to the LLM (truncated) |
| `ToolError(message, code=..., transient=False)` | Expected, user-presentable failure. `transient=True` allows one retry for idempotent tools |
| `Assessment(level, auto_ok=False, reason=None)` | Returned by `assess` for argument-dependent levels |

Registration: each tool module exposes `register(reg: ToolRegistry)`. `tools.build_registry()` calls it for `apps, browser, filesystem, terminal, email`, in that order. The registry is built once in the app lifespan.

Execution guarantees (from the executor): argument validation, dynamic level assessment, the permission policy and approvals, timeout, idempotent retry, crash containment, `tool.started`/`tool.finished` events, the audit log, and taint tracking. Tools must not re-implement these. See [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md#executor-agentexecutorpy).

## Built-in tools (31)

| Tool | Level | Timeout | Idempotent | Untrusted output | Notes |
|---|---|---|---|---|---|
| `open_application` | 1 | 20 s | | | Known apps, Start Menu `.lnk` index, Store apps (`Get-StartApps`), fuzzy match (cutoff 0.8) |
| `list_running_applications` | 0 | default | ✓ | | Processes with visible windows (+ background if asked), top 80 by memory |
| `close_application` | 3 | 20 s | | | `taskkill /IM` (with `/F /T` if `force`); protected processes refused |
| `open_url` | 1 | default | | | http/https/mailto only, via the default browser |
| `browser_open` | 1 | 45 s | | | Automation browser, optional URL in a new tab |
| `browser_search` | 1 | 45 s | ✓ | ✓ web | Top results (title, url, snippet) |
| `browser_navigate` | 1 | 45 s | | | http(s) only |
| `browser_read_page` | 0 | 30 s | | ✓ web | `main/article/[role=main]` or body text |
| `browser_extract_content` | 0 | 30 s | | ✓ web | links/headings/tables/forms/text |
| `browser_click` | 3 | 30 s | | | role+name, text, or CSS selector |
| `browser_fill` | 3 | 30 s | | | refuses `type=password` |
| `browser_screenshot` | 0 | 30 s | | | saved to `%APPDATA%\Jarvis\screenshots` |
| `browser_close` | 1 | default | | | |
| `search_files` | 2 | 30 s | ✓ | | name/glob, optional content search; 15 s / 60k entries walk budget |
| `list_directory` | 2 | default | ✓ | | |
| `read_file` | 2 | 45 s | ✓ | ✓ file | text (≤ 8 MB, no NUL in first 4 KB), PDF (pypdf), DOCX |
| `create_file` | 3 | default | | | refuses an existing file unless `overwrite` |
| `edit_file` | 3 | default | | | exact find/replace (ambiguity check) or append |
| `move_file` / `copy_file` | 3 | default | | | never overwrite |
| `delete_file` | 5 | default | | | Recycle Bin (`SHFileOperationW` + `FOF_ALLOWUNDO`); workspace roots refused |
| `run_command` | 4 (assessed) | 1900 s (process limited by `command_timeout_s`) | | ✓ process | no shell, see security doc |
| `run_project` | 4 (assessed, never auto) | 30 s | | ✓ process | background process, waits 4 s for early output |
| `get_process_output` | 0 | default | ✓ | ✓ process | **Jarvis-started processes only** |
| `inspect_process` | 0 | default | ✓ | | Jarvis process id or any PID (status, CPU, memory, redacted cmdline) |
| `stop_process` | 1 (Jarvis process) / 4 (other PID) | 20 s | | | kills tree; protected processes, own PID and parent PID refused |
| `search_emails` | 0 | 40 s | ✓ | ✓ email | Gmail query syntax |
| `read_email` / `read_thread` | 0 | 30 / 40 s | ✓ | ✓ email | |
| `summarize_emails` | 0 | 120 s | | ✓ email | one LLM call (summarization model) over ≤ 20 emails |
| `create_email_draft` | 3 | 40 s | | | draft only. **No send tool exists** |

"default" means `automation.tool_timeout_s` (60 s). An untrusted output taints the task, so every later level ≥ 3 action in that task asks for approval.

## How to add a new tool (worked example)

The example adds `clipboard_get_text`, a read-only tool that returns the clipboard's text via PowerShell's `Get-Clipboard`. It is a level 0 (read-only) action. Its output is user data that might contain injected instructions, so it is marked untrusted.

1. Create `backend/tools/clipboard.py`:

```python
"""Clipboard tools (Windows)."""
from __future__ import annotations

import asyncio
import subprocess

from pydantic import Field

from agent.tool_registry import ToolContext, ToolError, ToolInput, ToolRegistry, ToolResult

CREATE_NO_WINDOW = 0x08000000


class ClipboardGetIn(ToolInput):
    max_chars: int = Field(4000, ge=100, le=50000, description="Maximum characters to return")


def _read_clipboard() -> str:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"],
        capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW)
    if out.returncode != 0:
        raise ToolError("Could not read the clipboard.", code="clipboard_error", transient=True)
    return out.stdout or ""


def register(reg: ToolRegistry) -> None:
    @reg.tool("clipboard_get_text", "Read the current text on the Windows clipboard.",
              input_model=ClipboardGetIn, level=0, timeout_s=15, idempotent=True, category="clipboard")
    async def clipboard_get_text(args: ClipboardGetIn, ctx: ToolContext) -> ToolResult:
        text = await asyncio.to_thread(_read_clipboard)   # never block the event loop
        return ToolResult.success(
            f"Read {min(len(text), args.max_chars)} characters from the clipboard.",
            {"text": text[: args.max_chars], "truncated": len(text) > args.max_chars},
            untrusted=True, source="clipboard")
```

   Note that the tool calls `powershell.exe` through `subprocess` itself. The terminal tools' shell block applies only to `run_command`/`run_project`. Built-in tools may call fixed, non-model-controlled commands, as `apps.py` does with `Get-StartApps`. Never pass model-supplied strings into a shell.

2. Register the module in `backend/tools/__init__.py`:

```python
    from tools import apps, browser, clipboard, email, filesystem, terminal
    ...
    for module in (apps, browser, filesystem, terminal, email, clipboard):
        module.register(reg)
```

   `jarvis-backend.spec` collects every submodule of `tools`, so no PyInstaller change is needed.

3. Optional UI label: add `clipboard_get_text: 'Clipboard'` to `TOOL_LABELS` in `frontend/src/renderer/lib/status.ts`. Without it the label falls back to "clipboard get text". The tool appears automatically in Settings → Permissions (per-tool policy) through `GET /api/tools`.

4. Test it: add a pytest that builds the registry and runs the tool through `Executor`. Follow `tests/backend/test_tools.py` (the `ex` and `ctx` fixtures).

### Guidelines

- **Pick the lowest correct level** (see [PERMISSIONS_AND_SECURITY.md](PERMISSIONS_AND_SECURITY.md)). Anything irreversible is level 5.
- **Argument-dependent risk** → provide `assess`. Example from `terminal.py`: `Assessment(4, auto_ok=allowlisted and not risky, reason=...)`. `assess` may raise `ToolError` to block outright. The effective level is `max(tool.level, assessment.level)`, so assess can raise the level but never lower it below `tool.level`. That is why `stop_process` declares level 1 and assesses to 4 for foreign PIDs.
- **`describe=lambda args: "..."`** gives a one-line human summary for the approval dialog. Without it the first sentence of the description is used.
- **Paths**: always resolve them through `tools.pathguard.resolve(raw, ctx.settings, ctx.services.paths, must_exist=..., label=...)`.
- **Blocking work** (filesystem, subprocess, psutil, ctypes) goes in `asyncio.to_thread`. Heavy imports go inside the handler (lazy).
- **External content** (web, email, files, process output, clipboard…) → `untrusted=True` with a `source`.
- `idempotent=True` only for tools that are safe to run twice.
- Raise `ToolError` for expected failures. Let unexpected exceptions propagate: the executor reports them as `internal` with redaction.
- Keep `data` compact. It is truncated to `tool_result_char_limit` anyway.
- Long-lived resources belong on `Services` (see `agent/services.py`) and must be closed in the lifespan `finally` in `core/app.py`.

## How to add another AI provider

Jarvis is **Groq-specific** today:

- `ProviderManager` (`providers/manager.py`) is built with `base_url=GROQ_BASE` (`https://api.groq.com/openai/v1`), loads only `providers WHERE kind='groq'`, validates keys with Groq's `GET /models`, and inserts new rows with `kind='groq'`.
- `QuotaTracker.update_from_headers` interprets headers with **Groq's** semantics: `x-ratelimit-*-requests` = requests per day, `x-ratelimit-*-tokens` = tokens per minute. Other providers use the same header names with different meanings; OpenAI's `limit-requests`, for example, is per minute.
- `GroqClient` (`providers/groq_client.py`) calls the OpenAI-compatible `/chat/completions` (SSE streaming, tool calls) and `/audio/transcriptions`, and accounts audio with Groq's 10 s minimum.
- Callers use `services.groq` directly: `Orchestrator._llm_loop`, `VoiceService._transcribe_and_submit` and the `summarize_emails` tool.
- The UI section "Groq API keys" (`settings/GroqKeys.tsx`) and the error messages are Groq-specific. Model preferences (`ProviderSettings.models`) are plain model-id strings with no provider attached.

The `providers` table already has a `kind` column (default `'groq'`), which is the hook for a realistic path:

1. **Generalise the manager.** Add `kind` and `base_url` constructor parameters, filter `load()` by `kind`, and write `kind` in `add()`. Make the key-test endpoint configurable.
2. **Pluggable quota parsing.** Move the header-to-metric mapping into a per-provider strategy, for example `{"rpd": "requests", "tpm": "tokens"}` for Groq and `{"rpm": "requests", "tpm": "tokens"}` for an OpenAI-style API, and pass it to `QuotaTracker`. Keep quota scopes per provider so that scopes from different providers never collide (for example, prefix the scope with the kind).
3. **Client.** For an OpenAI-compatible provider, `GroqClient` can be reused against a second manager instance. Otherwise write a client exposing the same `chat(...) -> ChatResult` and `transcribe(...)` interface, including the streamed tool-call reassembly.
4. **Routing.** Introduce a small router service (for example `services.llm`) that maps a model id, or a `provider:model` string in `ModelPreferences`, to the right client, and replace the direct `services.groq` uses listed above.
5. **API and UI.** Add `kind` to `ProviderIn` and the dashboard, filter `/api/providers` by kind, and generalise the keys UI and its error texts.
6. **Tests.** Mirror `tests/backend/test_providers.py` with a mocked transport for the new provider.

None of this is implemented today.
