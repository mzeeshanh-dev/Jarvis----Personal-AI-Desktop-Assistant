# Architecture

## Process model

```
+------------------------------- Electron (Jarvis.exe) -------------------------------+
|  main process (frontend/src/main/main.ts)                                           |
|   - single-instance lock, tray, global shortcut, app:// protocol + CSP              |
|   - validated IPC handlers                                                          |
|   - BackendManager (backend.ts): spawn, health, heartbeat, restart, shutdown        |
|                                                                                     |
|   main window (app://jarvis/index.html)      float window (app://jarvis/index.html#/float)
|   React renderer, sandboxed, no Node         compact always-on-top assistant        |
|        |  window.jarvis (preload bridge)            |                               |
+--------|--------------------------------------------|-------------------------------+
         | HTTP REST + WebSocket on 127.0.0.1:<random port>                           
         | X-Jarvis-Token header / ?token=  + Origin allowlist                       
         v                                                                            
+------------------------- Python backend (jarvis-backend.exe / main.py) ------------+
|  uvicorn + FastAPI (core/app.py)  TokenAuthMiddleware -> CORS -> routes / ws       |
|  Services: Database(SQLite, 1 thread) SettingsStore EventBus SecretStore(keyring)  |
|            ProviderManager+GroqClient  ApprovalBroker  PerfMonitor                 |
|            BrowserManager(Playwright)  ProcessManager  GmailService  VoiceService  |
|  Orchestrator -> Planner / Executor -> ToolRegistry (31 tools)                     |
|  VoiceService: mic (sounddevice) -> VAD -> noise gate -> Groq Whisper -> agent     |
|                wake word (onnxruntime)      Piper TTS -> WAV served to renderer    |
+------------------------------------------------------------------------------------+
         |                         |                          |
   api.groq.com             gmail.googleapis.com      installed Edge/Chrome (Playwright)
```

Electron starts the backend as a child process and owns its lifetime. The backend binds only to loopback. `main.py` refuses any host other than `127.0.0.1`, `localhost` or `::1`, and exits with code 2 if given one. A watcher thread blocks on the parent process handle (`JARVIS_PARENT_PID`, via `psutil.Process(pid).wait()`) and stops the server if Electron dies, so the backend is never left orphaned.

## Major design decisions

| Decision | Where | Reason |
|---|---|---|
| **Python captures the microphone**, not the renderer | `backend/voice/audio.py`; `main.ts` denies every renderer permission request (`setPermissionRequestHandler(... cb(false))`, `setPermissionCheckHandler(() => false)`) | Push-to-talk and the wake word keep working while every window is hidden. The renderer never needs device permissions, so it can be locked down completely. The stream is opened only while needed and released when idle. |
| **The renderer plays TTS** | `voice/tts.py` caches WAV clips; `renderer/lib/playback.ts` plays `GET /api/tts/{clip}.wav` | The UI can pause, resume, stop and replay instantly without a round trip to the backend. Windows use `backgroundThrottling: false` so playback continues while hidden. Only the main window plays audio and reports `tts.state` back. |
| **`app://` custom protocol + per-launch token** instead of a fixed port / `file://` | `main.ts` `serveRenderer()`, `backend.ts` `freePort()` + `randomBytes(32)` | The renderer gets a stable, secure origin (`app://jarvis`) that the backend can allowlist, and a CSP header injected on HTML responses. The port is random each launch, so nothing collides and nothing can hard-code it. The token blocks other local processes and web pages from calling the API. |
| **PyInstaller onedir** (not onefile) | `backend/jarvis-backend.spec` | Fast startup and no extraction to a temp folder on every launch. The output folder is shipped as `resources/backend`. |
| **Installed Edge/Chrome through the Playwright `channel`** | `tools/browser.py` | No browser download and a smaller installer. A dedicated persistent profile (`%APPDATA%\Jarvis\browser-profile`) keeps the user's real profile, cookies and passwords out of reach. The `chromium` channel needs `playwright install chromium`, which is not bundled. |
| **Gmail via raw HTTPX** instead of `google-api-python-client` | `integrations/gmail.py` | Only a handful of REST endpoints are needed (messages, threads, drafts, profile, token). The reused `httpx.AsyncClient` avoids a large dependency tree and the bundle size and import cost it brings. |
| **NumPy spectral gate** instead of RNNoise | `voice/noise.py` | RNNoise has no maintained Windows Python wheels. The gate is dependency-free (NumPy only) and runs once per finished utterance. |
| **openWakeWord ONNX models run directly on onnxruntime** | `voice/wakeword.py` | Reimplements openWakeWord's streaming pipeline (melspectrogram → embedding → classifier) without the `openwakeword` package and its heavier dependencies. Audio never leaves the machine. |
| **Direct route for simple commands** | `agent/planner.py` `route_direct` | Commands like "open Chrome" or "search Google for X" run with zero LLM calls. They work with no Groq key and respond with the lowest latency. |
| **Single SQLite worker thread** | `storage/database.py` | The event loop never blocks and the connection is never shared across threads (WAL mode, `synchronous=NORMAL`). |
| **Secrets only in Windows Credential Manager** | `core/secrets.py` | SQLite stores opaque references (`groq:<id>`, `gmail:refresh_token`) and never the values. `WinVaultKeyring` is pinned explicitly because entry-point discovery can select a no-op backend inside a PyInstaller bundle. |
| **Event fan-out over one WebSocket** instead of polling | `core/events.py` | Every renderer receives the same event stream, and the UI updates only on events. |

## Startup sequence

1. `Jarvis.exe` takes the single-instance lock. A second launch focuses the existing window (`second-instance` → `showMain()`).
2. The `app://` scheme is registered as privileged (standard, secure, fetch, CORS).
3. `whenReady`: device permissions are denied, the `app://` handler is installed, IPC handlers are registered, and the main window and tray are created. `--hidden` (used by start-with-Windows) keeps the main window hidden.
4. `BackendManager.start()`:
   - picks a free port by listening on `127.0.0.1:0` and generates a 64-hex-char token;
   - spawns `backend/.venv/Scripts/python.exe backend/main.py --port N` (dev) or `resources/backend/jarvis-backend.exe --port N` (packaged) with `windowsHide: true`;
   - passes the environment `JARVIS_AUTH_TOKEN`, `JARVIS_DATA_DIR` (= Electron `userData`), `JARVIS_RESOURCES_DIR`, `JARVIS_ALLOWED_ORIGINS`, `JARVIS_PARENT_PID`, `PYTHONUNBUFFERED=1` and `PYTHONIOENCODING=utf-8`;
   - writes stdout/stderr to `%APPDATA%\Jarvis\logs\backend-console.log`, which is rotated to `.1` above 5 MB.
5. `main.py` probes the port. If the port is taken it exits with code **98**, and Electron immediately retries on a new port. Otherwise it configures logging and runs uvicorn (`ws="websockets-sansio"`, 4 MB max WS message, the Proactor loop that Windows needs for subprocesses and Playwright).
6. The FastAPI lifespan opens the DB (applying migrations), loads settings, builds every service and the tool registry, and records `startup_ms`.
7. Electron polls `GET /health` (1.5 s per try, every 250 ms, up to 45 s) until `ready: true`. It then sets the status to `ready`, starts a heartbeat every 20 s and broadcasts `backend:status` to the windows.
8. The renderer receives `BackendInfo {url, wsUrl, token}` and opens the WebSocket. On `ws.open` it loads settings and conversations.
9. Electron fetches `/api/settings` and applies the desktop settings (shortcut, tray, float, login item).

## Supervision and shutdown (`frontend/src/main/backend.ts`)

| Situation | Behaviour |
|---|---|
| Health check fails within 45 s | Process tree is killed; the exit is treated as a crash |
| Heartbeat (every 20 s, 5 s timeout) fails 3 times in a row | Process tree is killed (`taskkill /PID <pid> /T /F`); the exit is treated as a crash |
| Exit code 98 | Immediate restart on a new port (not counted as a crash; at most 10 port retries per minute, after which it is treated as a crash) |
| Unexpected exit | Restart with backoff `min(15 s, 1 s × 2^(n-1))`; status `restarting` |
| 5 crashes within 5 minutes | Status `failed` with the message "The backend crashed 5 times in 5 minutes. See logs/backend-console.log."; the UI shows **Retry** |
| Manual restart (IPC `backend:restart`) | Restart counter is cleared, then stop and start |
| Quit (`before-quit`) | Global shortcuts are unregistered. `POST /api/shutdown` (1.5 s timeout) is sent, Electron waits up to 4 s for a graceful exit, then runs `taskkill /T /F`. Then the tray is destroyed and `app.exit(0)` runs |

The backend lifespan shutdown cancels tasks, closes voice (mic and wake loop), kills managed processes, closes the browser, the Gmail client and the Groq client, and closes the DB.

## Frontend ↔ backend communication

### Authentication

- Every HTTP and WebSocket request must carry the per-launch token, either in the **`X-Jarvis-Token`** header or the **`?token=`** query parameter. The query parameter is what the WebSocket and `<audio>` clip URLs use. The comparison uses `hmac.compare_digest`.
- **Origin allowlist**: if an `Origin` header is present, it must be in `JARVIS_ALLOWED_ORIGINS`. In packaged builds that is `app://jarvis`; in dev the Vite origin is added. Requests without an `Origin` header (Electron main-process fetches, media element GETs) pass on the token alone.
- Public (no token): `GET /health` and `GET /api/gmail/oauth/callback`, which the browser opens during OAuth and is protected by the OAuth `state`. HTTP `OPTIONS` preflights are also passed through.
- Failure: HTTP → `401 {"detail":"unauthorized"}`; WebSocket → close code **4401**.
- If `JARVIS_AUTH_TOKEN` is unset (for example `python main.py` started by hand), the middleware is disabled. Electron always sets it.
- CORS: `CORSMiddleware` allows the same origins, all methods and headers, and no credentials. FastAPI's docs and OpenAPI endpoints are disabled.

### WebSocket `/ws`

On connect the server sends `hello`. Client messages are validated by the `ClientMessage` model (`type` ≤ 40 chars, `text` ≤ 8000, ids ≤ 64). Invalid messages get `{"type":"error","error":"invalid message"}`.

**Client → server**

| `type` | Fields | Effect |
|---|---|---|
| `ping` | – | Replies `pong` |
| `chat.send` | `text`, `conversation_id?` | Submits a task (source `text`); replies `chat.accepted {task_id}` or `error` |
| `task.cancel` | `task_id` | Cancels one task |
| `task.cancel_all` | – | Cancels all tasks, cancels the voice session, emits `tts.stop` |
| `approval.respond` | `id`, `approved` | Resolves a pending approval |
| `tts.state` | `playing` | Main window reports playback; the server broadcasts `tts.playing` |
| `ui.conversation` | `conversation_id` | Sets the conversation that voice follow-ups go into |
| `voice.toggle` | `conversation_id?` | Push-to-talk toggle |
| `voice.start` / `voice.stop` / `voice.cancel` | `conversation_id?` (start) | Start, finish or abort a voice session |
| anything else | – | `{"type":"error","error":"unknown message type …"}` |

**Server → client** (all `EventBus.emit` events include `ts`)

| Event | Payload | Emitted by |
|---|---|---|
| `hello` | `version, active_tasks, approvals, voice` | `api/ws.py` on connect |
| `pong`, `chat.accepted`, `error` | see above | `api/ws.py` (to the sender only) |
| `task.started` | `task_id, conversation_id, text, source` | orchestrator |
| `task.status` | `task_id, phase (thinking/executing/awaiting_approval), detail, iteration?` | orchestrator, executor |
| `assistant.delta` | `task_id, conversation_id, stream_id, text` | streamed LLM tokens |
| `assistant.message` | `task_id, conversation_id, content, status` | final reply |
| `task.finished` | `task_id, conversation_id, status, route, llm_calls, tool_calls, duration_ms` | orchestrator |
| `task.cancelled` | `task_id, conversation_id` | task manager |
| `tool.started` | `task_id, call_id, tool, level, arguments` (redacted, truncated) | executor |
| `tool.finished` | `task_id, call_id, tool, ok, summary, duration_ms` | executor |
| `approval.request` | `id, task_id, tool, level, level_name, reason, summary, arguments, timeout_s` | approval broker |
| `approval.expired` | `id, task_id` | approval broker (timeout) |
| `voice.state` | `state (idle/listening/transcribing/error), wake_word, paused, source?, error?` | voice service |
| `voice.level` | `level` 0..1 (~12 Hz while listening) | voice service |
| `voice.transcript` | `text, duration_s` | voice service |
| `voice.no_speech` | – | voice service |
| `voice.error` | `error` | voice service |
| `wakeword.detected` | `score` | voice service |
| `tts.ready` | `clip_id, task_id, text, kind (response/ack/test)` | voice service (Piper) |
| `tts.system` | `text, rate, task_id, kind` | voice service (fallback to the Windows voice) |
| `tts.stop` | – | barge-in, cancel-all, `/api/tts/stop` |
| `tts.playing` | `playing` | relay of `tts.state` |
| `providers.changed` | – | provider manager (renderer re-fetches the dashboard) |
| `gmail.changed` | – | Gmail routes |
| `settings.changed` | `settings` | settings store listener |

The renderer client (`renderer/lib/api.ts`) also emits the synthetic local events `ws.open` and `ws.close`. It reconnects with backoff `min(8 s, 300 ms × 2^n)`.

### REST endpoints (`backend/api/routes.py`)

All endpoints require the token except the two public ones.

| Method & path | Purpose |
|---|---|
| `GET /health` | `{ok, version, ready}` (public) |
| `POST /api/shutdown` | Graceful stop (sets uvicorn `should_exit`) |
| `GET /api/state` | Version, settings, voice status, active tasks, approvals, Gmail status, provider count, data dir, level names |
| `GET /api/settings` / `PUT /api/settings` | Read / deep-merge a partial patch (422 with `loc`/`msg` on validation errors) |
| `POST /api/settings/reset` | Reset to defaults |
| `GET /api/conversations?q=&limit=` | List or search conversations (limit ≤ 500) |
| `GET /api/conversations/{id}/messages` | Stored messages for the UI |
| `PATCH /api/conversations/{id}` | Rename (`title` 1–120) |
| `DELETE /api/conversations/{id}` / `DELETE /api/conversations` | Delete one / all |
| `POST /api/tasks` | Submit `{text, conversation_id?}` → `{task_id}` |
| `GET /api/tasks?limit=` | `{active, history}` |
| `POST /api/tasks/{id}/cancel` / `POST /api/tasks/cancel-all` | Cancel |
| `POST /api/approvals/{id}` | `{approved}` |
| `GET /api/logs?limit=&task_id=` | Execution audit log |
| `GET /api/logs/backend?lines=` | Tail of `backend.log` (≤ 5000 lines) |
| `GET /api/tools` | Tool catalogue (name, description, level, category, available, schema) |
| `GET /api/processes` | Jarvis-managed processes |
| `GET /api/perf` | Performance snapshot |
| `GET /api/providers?model=` | Groq dashboard |
| `POST /api/providers` | Add a key `{label, api_key, quota_scope}` and test it immediately |
| `PATCH /api/providers/{id}` / `DELETE /api/providers/{id}` | Update (label/enabled/scope/api_key/sort_order) / delete |
| `POST /api/providers/{id}/test` | Validate with `GET /models` |
| `GET /api/models` | Models available to the configured keys |
| `GET /api/voice/status`, `GET /api/voice/devices` | Voice status / input devices |
| `POST /api/voice/test-mic` | 0.6 s mic check (detects all-zero input) |
| `POST /api/voice/toggle` / `start` / `stop` / `cancel` | Voice session control (toggle is what the global shortcut calls) |
| `POST /api/voice/pause` | `{paused}`: pauses the wake word (tray "Pause listening") |
| `POST /api/voice/speak` | Speak a test sentence |
| `POST /api/tts/stop` | Broadcast `tts.stop` |
| `GET /api/tts/{clip_id}.wav` | Piper clip (404 once it falls out of the 12-clip cache) |
| `GET /api/gmail/status` | `{client_configured, connected, account}` |
| `POST /api/gmail/client` / `DELETE /api/gmail/client` | Save OAuth client (id+secret or pasted JSON) / remove client and authorization |
| `POST /api/gmail/connect` | Returns `auth_url` |
| `GET /api/gmail/oauth/callback` | OAuth redirect target (public) |
| `POST /api/gmail/disconnect` | Revoke and delete the refresh token |
| `POST /api/data/clear` | Clear selected data (see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)) |

When voice is disabled (`create_app(enable_voice=False)`, used in tests), the voice endpoints return `503`.

### Electron IPC (preload bridge `window.jarvis`)

See [PERMISSIONS_AND_SECURITY.md](PERMISSIONS_AND_SECURITY.md#electron-hardening) for validation details. Channels: `backend:info`, `backend:restart`, `app:version`, `app:metrics`, `window:show-main`, `window:show-float`, `window:hide-float`, `window:minimize`, `desktop:apply`, `shortcut:status`, `shell:open-external`, `shell:open-folder`. Main → renderer events: `backend:status`, `navigate`, `shortcut:status`.

## Windows and tray

- **Main window**: 1200×800 (min 920×600), hidden title bar with overlay. With `close_to_tray` (default on), closing hides the window instead of quitting. With `minimize_to_tray` (default on), minimizing also hides it to the tray (a one-time balloon explains this). The backend (global shortcut, wake word, running tasks) is unaffected by window state. Reopening restores the window *before* showing it, since showing a minimized-then-hidden window would re-fire `minimize`. `Jarvis.exe --quit` (or `electron.exe <project> --quit` in launcher mode) exits a running instance.
- **Float window** (`#/float`): 440×148, frameless, transparent, no taskbar entry, `alwaysOnTop` level `floating` (setting `float_always_on_top`), shown with `showInactive()` at the bottom centre of the primary display. When the global shortcut fires and the main window isn't focused, it appears if `show_float_on_voice` is on.
- **Tray menu**: Open Jarvis, Activate voice (shows the shortcut), Pause listening (checkbox → `/api/voice/pause`), Show floating assistant, Settings, Exit Jarvis.
- **Global shortcut**: default `Control+Space`, calls `POST /api/voice/toggle`. Registration failures are reported to the UI as a banner (see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)).
