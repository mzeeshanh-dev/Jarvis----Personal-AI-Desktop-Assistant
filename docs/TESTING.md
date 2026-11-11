# Testing

Three suites. **No live credentials are needed.** Groq is mocked with `httpx.MockTransport` (`GroqMock` in `tests/backend/conftest.py`), secrets use the in-memory `MemorySecretStore`, and Gmail tests use a mocked HTTPX client.

| Command | Suite | Runner |
|---|---|---|
| `npm test` | frontend + backend | – |
| `npm run test:frontend` | `tests/frontend/**/*.test.ts(x)` | vitest (jsdom), `frontend/vitest.config.mts` |
| `npm run test:backend` | `tests/backend` | pytest via the venv (`scripts/run-python.mjs`, `PYTHONPATH=backend`), `-q` |
| `npm run test:e2e` | `tests/e2e/app.spec.ts` | Playwright `_electron`, after `build:frontend` |
| `npm run typecheck` | TypeScript | `tsc --noEmit` |

Prerequisites: `npm install` and `npm run setup` (the backend venv). Tests that need voice models are **skipped** when the models are absent (Silero VAD, wake word, Piper). The backend tests use Windows facilities (Recycle Bin, `taskkill`, process listing), so run them on Windows.

## Backend (pytest)

Config: `tests/backend/pytest.ini` (`asyncio_mode = auto`, function-scoped loops, DeprecationWarnings ignored). Dev requirements include `pytest-timeout`.

### Fixtures (`conftest.py`)

- `groq`: `GroqMock`, a scriptable queue of `(status, headers, body)` responses or callables. `/models` returns a fixed list, and the key `bad-key-000` gets a 401. Helpers `rl_headers()`, `sse()`, `text_completion()` and `tool_completion()` build realistic rate-limit headers and SSE streams.
- `paths` (tmp data dir, repo resources), `db` (migrated SQLite), `secrets` (memory), `events`.
- `workspace`: a folder under `tests/.work/<id>` (git-ignored). pytest's `tmp_path` lives under `%LOCALAPPDATA%`, which the path guard protects.
- `settings` (workspace root = that folder), `clock` (monkeypatched `time.time`), `make_manager` (a `ProviderManager` with the mock transport and no-op sleep).

### Files

| File | Covers |
|---|---|
| `test_agent.py` | Planner direct routes ("Open Chrome.", "Jarvis, please open VS Code", "open youtube", domains, "go to …"), direct web search, complex phrasing goes to the LLM, model tier choice; permission `evaluate` levels; executor argument validation, approval flow, approval timeout denies, tool timeout, untrusted output taints the context; context orphan removal and trimming; orchestrator LLM tool loop end to end (mock Groq), direct route makes no LLM call, task cancellation, clear message with no provider |
| `test_providers.py` | `parse_duration`; threshold freeze at 97% (RPD) and TPM using the token reset; configurable threshold; freezes per model and scope; single-probe recovery; 429 `retry-after`; local RPM and audio-seconds estimates; fair round-robin; disabled/invalid keys skipped; 429 failover to another scope; shared-scope freeze; threshold freeze switches scope then recovers; 5xx/timeout retry then circuit open; 400 not retried; 403 forbids the model for that key; no-keys message; key test lists models; unsupported model ineligible; secrets never in the database; usage accounting and dashboard; quota state persists across restart |
| `test_tools.py` | Path guard: inside allowed, escapes denied, sensitive files denied, protected roots denied even if configured; file tool round trip; PDF reading; outside-workspace blocked; delete requires approval and uses the Recycle Bin; shells blocked; allowlist assessment; `run_command` output and exit code, timeout kills, cancellation kills; `run_project` + output; cwd outside workspace blocked; `open_url` rejects dangerous schemes; closing protected processes refused; listing running apps; app index resolves built-ins; browser URL validation |
| `test_voice_api.py` | WAV encoding; RMS bounds; noise reduction attenuates noise and keeps signal; Silero silence is low (skipped without model); energy VAD; VAD fallback; wake word runs and ignores noise (skipped without models); Piper synthesis (skipped without model); push-to-talk pipeline with a fake microphone → mocked Whisper → task with `source=voice`; `/health` public and REST needs the token; WebSocket requires token **and** allowed Origin (`hello`, `ping`/`pong`, unknown type → `error`); settings validation (422) and persistence; provider API never returns the secret; clear data; redaction; Gmail body extraction; Gmail search and draft (mocked HTTP); OAuth state validated |

## Frontend (vitest)

| File | Covers |
|---|---|
| `tests/frontend/reducer.test.ts` | Task event flow (adopts conversation, streams deltas, records tools, finalizes), new stream id replaces the draft, ignores other conversations, approvals queue/expire/clear on task end, voice state/level/errors, offline orb, toast cap, `rowsToMessages` folding tool calls into replies, phase/duration labels |
| `tests/frontend/markdown.test.tsx` | Lists, code and emphasis; **never injects HTML** from model output; links go through the handler instead of navigating; `BackendClient` sends `X-Jarvis-Token` and surfaces API errors |

## End to end (Playwright + Electron)

`tests/e2e/app.spec.ts` launches the **real Electron app** (`electron.launch({ args: [ROOT, --user-data-dir=tests/.work/e2e-<ts>] })`) with `JARVIS_DEV_SERVER_URL` cleared, so the built `app://` renderer is used. It needs `backend/.venv` (the dev backend) and no Groq key. Config: `tests/e2e/playwright.config.ts` (one worker, 120 s timeout). Tests:

1. The window is secure and branded: title, `app://jarvis/` URL, no `require` or `process` in the renderer, bridge present, `contextIsolation`, `sandbox`, `nodeIntegration=false`.
2. The backend rejects requests without the launch token (401).
3. A text command without a Groq key shows "No Groq API key is configured" and creates a conversation.
4. Every settings section renders; the Groq and Permissions contents appear.
5. The global shortcut `Control+Space` is registered, **or** a conflict error is reported.
6. Backend crash detection: kills the Python process tree, waits for a new PID, status `ready`, `restarts ≥ 1`.
7. Quitting stops the backend and Electron (no orphan processes).

It also logs "cold start to connected UI: N ms".

## Writing new tests

- Backend: use the fixtures above. Script Groq responses with `groq.push(...)`, and use `clock.advance()` for time-based quota logic. Never call real Groq or Gmail.
- Put workspace files under the `workspace` fixture, not `tmp_path` (the path guard blocks `%LOCALAPPDATA%`).
- Frontend: keep logic in pure modules (`reducer.ts`, `status.ts`, `markdown.tsx`) so it can be tested without Electron.

## Live tests (real Groq API)

`tests/live/test_live_groq.py` runs only when `JARVIS_LIVE_KEY_FILE` points to a file containing a Groq key. It uses a temporary data directory and an in-memory secret store, so it writes nothing to `%APPDATA%\Jarvis` or Credential Manager. It costs about 15 chat requests and 2 transcriptions.

```powershell
$env:JARVIS_LIVE_KEY_FILE = "..\.groq"
node scripts/run-python.mjs -m pytest tests/live -v -s
```

Covers: streaming plus real rate-limit headers parsed into quota state; the agent finding and reading a file; level-3 file creation via approval; follow-up context; a declined level-5 delete; an allowlisted terminal command; a prompt-injection file (the model must not delete anything); Whisper on Piper-synthesized speech; the full voice pipeline (fake mic, VAD, denoise, Whisper, agent); the direct route with 0 LLM calls.

## Packaged-app E2E

`$env:JARVIS_E2E_EXE = "dist-installer\win-unpacked\Jarvis.exe"; npm run test:e2e` runs the same 9 E2E tests against the packaged application (frozen backend, bundled models, asar).
