# Implementation progress

Status as of 2026-11-04. Use this file to resume work: it records what is built, what was verified and how, measured numbers, and what remains.

## Environment (inspected at start)

| Item | Value |
|---|---|
| OS | Windows 11 Pro (build 26300) |
| CPU / RAM | AMD Ryzen 5 5500U (12 threads), 16 GB (11.8 GB visible) |
| Node / npm | 26.13.0 / 11.20.0 |
| Python | 3.11.4 (`py -3.11`), venv at `backend/.venv` |
| Disk free | ~180 GB |
| Microphone | Realtek Microphone Array: detected and opened (Silero/energy VAD received audio) |
| Packaging tools | No WiX/.NET installed. electron-builder downloads NSIS and WiX on demand |

## Checklist

| Phase | Item | State |
|---|---|---|
| 1 | Monorepo (`frontend/ backend/ docs/ scripts/ resources/ tests/ installer/`), root `package.json` | ✅ |
| 2 | Electron + React + TS + Vite + Tailwind UI; FastAPI backend; REST + WebSocket; `npm run dev` | ✅ |
| 2 | Electron supervises the backend: dynamic port, per-launch token, health wait, heartbeat, crash restart, clean shutdown | ✅ E2E-verified |
| 3 | Push-to-talk (global shortcut + UI button), Silero VAD, noise reduction, Groq Whisper STT, Piper TTS, barge-in, playback controls | ✅ (STT needs a Groq key) |
| 4 | Orchestrator, planner (direct route), executor, tool registry, permissions + approvals, task manager, context, conversations | ✅ |
| 5 | Application, browser (Playwright on Edge/Chrome), filesystem, terminal tools | ✅ |
| 6 | Groq multi-key manager: round-robin, quota scopes, 97 % freeze, 429/401/403/404/5xx/timeout handling, circuit breaker, probe recovery, dashboard | ✅ (mock-tested; live 401 path verified against api.groq.com) |
| 7 | Gmail (OAuth PKCE loopback, search/read/thread/summarize/draft) | ✅ (needs your Google OAuth client) |
| 7 | Wake word "Hey Jarvis" (openWakeWord ONNX, local) | ✅ (opt-in) |
| 8 | Settings (11 sections), history, task log, audit log, performance monitor, data clearing | ✅ |
| 9 | Tests, security review, docs, PyInstaller backend, NSIS installer, MSI | see below |

## Test results

| Suite | Command | Result |
|---|---|---|
| Backend (pytest, Groq mocked) | `npm run test:backend` | **106 passed**, 0 failed, 0 skipped |
| Frontend (Vitest) | `npm run test:frontend` | **13 passed** |
| Electron E2E (Playwright over CDP, real backend) | `npm run test:e2e` | **9 passed** |
| Frozen backend smoke test | manual (see PRODUCTION_BUILD.md) | ready, 31 tools, Silero + Piper found, Credential Manager write/delete OK, live Groq 401 detected |

Coverage of the requested test list:

| # | Area | Where |
|---|---|---|
| 1 | Backend initialization | `test_voice_api.py::test_health…`, all TestClient tests (lifespan) |
| 2 | Electron initialization | E2E `beforeAll` (cold start → connected UI) |
| 3 | WebSocket communication | `test_agent.py` orchestrator tests, `test_websocket_requires_token_and_origin` |
| 4 | Global keyboard shortcut | E2E `global push-to-talk shortcut…` (registered: `Control+Space`) |
| 5 | Microphone activation | E2E `voice activation from the UI…`; Settings → Voice → Test |
| 6 | Voice processing | `test_push_to_talk_pipeline`, VAD / noise / wake-word / Piper tests |
| 7 | LLM tool calling | `test_orchestrator_llm_tool_loop` (mocked streaming tool calls) |
| 8 | Tool argument validation | `test_executor_validates_arguments` |
| 9 | Permission enforcement | `test_permission_levels`, approval/decline/timeout/taint tests |
| 10 | Application launching | `test_direct_route_uses_no_llm` (launcher mocked), `test_app_index_resolves_builtin` |
| 11 | Browser automation | `test_browser.py` (real headless Edge, local page) |
| 12 | Filesystem restrictions | pathguard + file tool tests |
| 13 | Terminal timeouts / cancellation | `test_run_command_timeout_kills`, `test_run_command_cancellation_kills`, blocked shells |
| 14 | Groq round-robin | `test_round_robin_is_fair` |
| 15 | 97 % freezing | `test_threshold_freezes_at_97_percent`, `test_tpm_threshold…`, `test_threshold_is_configurable` |
| 16 | Shared org quota | `test_shared_org_scope_freezes_all_its_keys`, `test_threshold_freeze_switches_scope_then_recovers` |
| 17 | Automatic recovery | `test_recovery_requires_single_probe`, `test_threshold_freeze_switches_scope_then_recovers` |
| 18 | HTTP 429 | `test_429_fails_over_to_other_scope`, `test_429_uses_retry_after` |
| 19 | Invalid keys | `test_disabled_and_invalid_keys_are_skipped`, `test_test_key_validates…` |
| 20 | Backend crash recovery | E2E `backend crash is detected and restarted` |
| 21 | Application shutdown | E2E `Jarvis --quit stops the app and its backend` |
| 22 | Installer packaging | see "Packaging" below |

## Measured performance

See [PERFORMANCE.md](PERFORMANCE.md#measured-results). Headlines: backend idle at about 81 MB and 0 % CPU; E2E cold start to a connected UI 3.4 s (dev build); wake word about 5.4 % of one core; direct commands make 0 LLM calls. Piper's cold load is 10 s, so it is pre-warmed when a voice session starts.

## Packaging

| Artifact | Command | Result |
|---|---|---|
| Frozen backend (PyInstaller onedir) | `npm run build:backend` | ✅ `build/backend/jarvis-backend/` (247 MB): smoke-tested (ready, 31 tools, Silero/Piper/Playwright driver/PortAudio bundled, Credential Manager OK) |
| NSIS installer | `npm run package:win` | ✅ `dist-installer/Jarvis-Setup-0.1.0.exe` (266.7 MB), per-user, no admin |
| Packaged app (`win-unpacked`) | `$env:JARVIS_E2E_EXE=...; npm run test:e2e` | ✅ 9/9 E2E pass against the packaged app (first launch 26.9 s, including Windows' first-run scan of the new executables) |
| MSI | `npm run package:msi` | Builds with electron-builder's WiX target. WiX compression of the ~270 MB payload takes 10–25 min. See the status note below |
| Signed-runtime launcher | `npm run shortcut` | ✅ Desktop and Start-menu shortcuts. Started and verified with Smart App Control **on**: window, backend, WebSocket, no Code Integrity blocks |

**Smart App Control (SAC) on this PC is On.** SAC has no per-app allowlist. During one build it blocked electron-builder from loading the freshly built, unsigned `Jarvis-Setup-0.1.0.exe` (Code Integrity events 3033/3077); a retry was not blocked. Unsigned installers and executables with no reputation may be blocked when run, so the installer was **not** installed on this machine. Options, in order of preference:

1. **Launcher mode** (`npm run shortcut`): runs Jarvis through the official Electron 45.2.2 `electron.exe` (unsigned but a widely distributed binary with cloud reputation) and the venv's `python.exe` (signed by the Python Software Foundation). Verified working with SAC on. "Start with Windows" works in this mode too.
2. **Code signing** for distribution: SAC trusts binaries signed by a certificate from a CA in the Microsoft trusted root program (e.g. Azure Trusted Signing, OV/EV certificates). Self-signed certificates do not satisfy SAC. electron-builder's `win.signtoolOptions` / Azure signing settings are the place to add it.
3. Turning SAC off is possible but removes a protection layer; it is not required.

MSI status: the first attempt was interrupted when a session ended. A rebuild was started on 2026-11-05; the result is recorded below when it finishes.

## Bugs found and fixed during verification

- **Blank window when `JARVIS_DEV_SERVER_URL` was set but empty**: `??` treated `''` as a dev URL. It now uses `|| undefined`.
- **Launch token written to `backend.log`** by uvicorn's WebSocket access line. Redacted; there is now a regression test and an E2E check.
- **`ELECTRON_RUN_AS_NODE` inherited from VS Code terminals** made `npm run dev` start Electron as plain Node. It is now stripped.
- **Cancelling during an approval could be swallowed** (Python 3.11 `wait_for` race), so the task finished as "failed". The task is now cancelled before the approval is resolved.
- **Noise reduction was too weak** (25 % noise removed). Retuned to about 63 % removed with 92 % of the signal kept.
- **Playwright driver leaked** when the user closed the automation window. **Wake word got stuck** after a mic error. **Port-retry loop was unbounded.** **Rejected shortcuts were still saved.** **Allowlisted `git log --output=…` ran without approval.** **Credential files were copied inside folders.** All fixed.

## Remaining limitations / manual configuration

1. **Groq API key required** for the LLM agent and voice transcription: Settings → Groq API keys. Direct commands (open app, open site, web search) work without it.
2. **Gmail requires your own Google Cloud OAuth "Desktop app" client** (Settings → Gmail has the steps). Not tested against a live Google account.
3. **Live Groq behaviour** (real rate-limit headers, real tool calling) has not been exercised with a valid key here; that logic is covered by mocked tests. The live API was reached for the invalid-key (401) path.
4. **Wake phrase is "Hey Jarvis"** (pretrained openWakeWord model). A bare "Jarvis" is not reliable. No custom model was trained.
5. **Speaker verification is not implemented.** It is optional in the spec, and speaker identity is never used for authorization.
6. **RNNoise is not used** (no maintained Windows wheels). A NumPy spectral gate is used instead.
7. **Urdu / Roman Urdu**: the language setting is passed to Whisper; prompts and routing are English-tuned.
8. **No email send tool**, by design: Jarvis creates drafts only.
9. **Terminal output inspection** works only for processes Jarvis started (`run_command` / `run_project`).
10. **Installers are not code-signed**, so SmartScreen will warn ("More info → Run anyway").
11. **Dev and installed builds share `%APPDATA%\Jarvis`.**
12. Changing "Concurrent tasks" applies to newly submitted tasks only.
