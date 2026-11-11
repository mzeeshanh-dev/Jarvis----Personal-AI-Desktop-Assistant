# Performance

Jarvis is meant to sit idle in the tray at near-zero cost and respond quickly when used. This page lists the design rules the code actually implements, the metrics it collects, and how to measure.

## Implemented design rules

| Rule | Where |
|---|---|
| **Lazy imports of heavy modules.** Playwright (first browser tool), sounddevice/PortAudio (first mic use), Piper and its voice model (first synthesis), onnxruntime sessions for VAD and wake word (first use), pypdf (first PDF read), keyring (SecretStore creation), psutil in tools, the voice package itself (`create_app` imports `voice.pipeline` inside the lifespan) | `tools/browser.py`, `voice/audio.py`, `voice/tts.py`, `voice/vad.py`, `voice/pipeline.py`, `tools/filesystem.py`, `core/secrets.py` |
| **No polling, with two exceptions.** Electron's **20 s backend heartbeat** (`HEARTBEAT_MS`), and the Performance settings panel's **3 s refresh, only while that panel is open** (it can also be switched off with its *Live* toggle). Everything else is event-driven over the WebSocket (`providers.changed`, `gmail.changed`, `task.*`, `voice.*`, …). The Groq dashboard and the approval dialog use local 1 s timers for countdowns without contacting the backend | `frontend/src/main/backend.ts`, `settings/Diagnostics.tsx`, `settings/GroqKeys.tsx` |
| **Parent watcher without polling.** The backend blocks a thread on `psutil.Process(parent).wait()` | `backend/main.py` |
| **Microphone released when idle.** The input stream is opened per push-to-talk session and closed when no subscriber remains. It stays open only while the wake word is enabled | `voice/pipeline.py` `_release_mic_if_idle`, `voice/audio.py` |
| **Noise reduction once per utterance**, not continuously; VAD and wake-word ONNX sessions limited to 1 thread each | `voice/noise.py`, `voice/vad.py`, `voice/wakeword.py` |
| **Reused HTTPX clients.** One `httpx.AsyncClient` for Groq (in `ProviderManager`) and one for Gmail, kept for the backend's lifetime (connection reuse) | `providers/manager.py`, `integrations/gmail.py` |
| **A single browser context**, started on first use and reused across tool calls | `tools/browser.py` `BrowserManager` |
| **Direct route for simple commands.** "Open X", "go to site", "search Google for …" run with **zero LLM calls** | `agent/planner.py`, `agent/orchestrator.py` |
| **Small model for short requests**, the large model only for complex ones; output budget 1024 vs. 2048 tokens | `planner.choose_model` |
| **Streaming responses.** LLM tokens are forwarded as `assistant.delta` while generating | `providers/groq_client.py` |
| **Parallel read-only tools.** Multiple level ≤ 2 tool calls in one LLM turn run concurrently | `orchestrator._execute` |
| **Bounded context.** History turns, character budget and tool-result size limits | `context_manager.py`, `result_formatter.py` |
| **One summarization call** for many emails (`summarize_emails`) instead of reading them one by one; Gmail metadata fetched 8 at a time | `tools/email.py`, `integrations/gmail.py` |
| **App index built once per session.** The Start Menu `.lnk` scan and `Get-StartApps` run lazily on the first non-builtin lookup | `tools/apps.py` `AppIndex` |
| **SQLite on one worker thread** with WAL | `storage/database.py` |
| **PyInstaller onedir.** No temp extraction at each launch | `backend/jarvis-backend.spec` |
| **Renderer kept awake** (`backgroundThrottling: false`) only so TTS and events keep flowing while hidden; the renderer does no periodic work of its own beyond the items above | `main.ts` |
| **TTS cache** of 12 clips; replay reuses the clip URL | `voice/tts.py`, `renderer/lib/playback.ts` |

## Metrics collected

`PerfMonitor` (`core/perf.py`) keeps the last 200 samples per stage and computes `count, avg, p50, p95, last` on request only. There is no background sampling.

| Sample | Recorded in |
|---|---|
| `e2e_ms` | whole task (orchestrator `_finish`) |
| `llm_ms` | each chat completion (orchestrator, summarize_emails) |
| `tool_ms` | each tool execution (executor) |
| `stt_ms` | Groq transcription |
| `voice_preprocess_ms` | voice handling outside the STT call (noise reduction, WAV encoding, accounting) |
| `tts_ms` | Piper synthesis |
| `startup_ms` | backend lifespan start → ready |

`GET /api/perf` returns the backend process (pid, CPU %, RSS MB, threads, uptime, startup_ms), its child processes (memory), system CPU and memory, the latency stats, plus active tasks, WS clients, running managed processes, browser state and voice status (`mic_open`, `wake_word_running`). The Performance panel combines this with Electron's `app.getAppMetrics()` (via IPC `app:metrics`).

## How to measure

- **In the app**: Settings → Performance (live while open).
- **Benchmark script**: `backend/.venv/Scripts/python.exe backend/scripts/benchmark.py` prints JSON for: backend cold start to `ready`, idle RSS and 5 s idle CPU, `/api/state` p50/p95, Silero load and per-frame cost, wake-word load and CPU per 10 s of audio, noise reduction on 5 s of audio, Piper first and subsequent synthesis, and local tool latencies (list_directory, search_files, read_file, list_running_applications, app index resolve). It makes no network or Groq calls. Voice measurements need the models from `npm run setup`.
- **E2E**: `tests/e2e/app.spec.ts` logs "cold start to connected UI: N ms".

## Measured results

Measured on the target machine (AMD Ryzen 5 5500U, 12 threads, 16 GB RAM with about 11.8 GB visible to Windows, integrated Radeon) on 2026-11-04 with `backend/scripts/benchmark.py`, the frozen-backend smoke test and the pytest suite. There was no network or Groq traffic, so LLM and STT latency are not included: they depend on Groq and appear live in **Settings → Performance**.

| Measurement | Result | Target |
|---|---|---|
| Backend cold start → `/health` ready (dev venv) | 2.2–2.7 s | < 5 s |
| Backend app startup inside lifespan (frozen exe) | 0.88 s | < 2 s |
| Frozen exe first launch → ready (includes first-run Defender scan) | 8.0 s | first run only |
| Backend idle RAM (process tree, dev) | 81 MB | < 150 MB |
| Frozen backend RAM after startup | 104 MB | < 150 MB |
| Backend idle CPU (5 s sample, no wake word) | 0.0 % | ≈ 0 % |
| `/api/state` latency p50 / p95 | 7.5–27 ms / 31–139 ms | < 50 ms p50 |
| Silero VAD per 32 ms frame | 0.57 ms (≈ 1.8 % of one core while listening) | < 2 ms |
| Wake word (openWakeWord) processing | 542 ms per 10 s audio ≈ **5.4 % of one core** (≈ 0.45 % of total CPU) | < 10 % of a core |
| Noise reduction, 5 s utterance | 45 ms | < 100 ms |
| Piper first synthesis (includes loading voice + espeak) | 10.3 s cold | pre-warmed (see below) |
| Piper warm: short sentence / two-sentence reply | 133 ms / 639 ms | < 1 s |
| `list_directory` / `read_file` / `search_files` (repo) | 9 ms / 7 ms / 64 ms | < 200 ms |
| `list_running_applications` | 822 ms | < 1.5 s |
| Direct route (e.g. "open Chrome") | 0 LLM calls (verified by test) | 0 LLM calls |

Because Piper's cold load is slow (10 s), `VoiceService` pre-warms the voice in a background thread when a voice session starts. It loads while you are still speaking and the audio is being transcribed, so the cost stays off the reply path. Typed commands with "also speak typed replies" enabled still pay the cold load once per backend start.
