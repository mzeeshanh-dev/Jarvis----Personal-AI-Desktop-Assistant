# Jarvis technical documentation

Jarvis is a Windows desktop AI agent. An Electron + React shell supervises a local Python (FastAPI) backend. The backend runs the agent loop, the tools, the voice pipeline and the Groq provider manager. These documents describe only what the code in this repository implements. Wherever something is not implemented, the docs say so.

Audience: developers who maintain and extend Jarvis, and the owner, who installs and configures it.

## Index

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Process model, major design decisions and the reasons for them, frontend↔backend protocol (auth, WebSocket messages, REST endpoints) |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | Repository layout, what each file does, runtime data locations |
| [INSTALLATION.md](INSTALLATION.md) | Installing the Windows build, first-run configuration (Groq keys, Gmail, workspace folders), uninstall/upgrade behaviour |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Prerequisites, `npm run` scripts, dev loop, environment variables |
| [PRODUCTION_BUILD.md](PRODUCTION_BUILD.md) | Frontend (Vite/esbuild) and backend (PyInstaller onedir) production builds |
| [WINDOWS_MSI_PACKAGING.md](WINDOWS_MSI_PACKAGING.md) | electron-builder NSIS and MSI targets, artifact names, upgrades, code-signing status |
| [VOICE_PIPELINE.md](VOICE_PIPELINE.md) | Microphone capture, VAD, wake word ("Hey Jarvis"), noise reduction, Whisper STT, Piper/system TTS |
| [AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md) | Task lifecycle, direct route vs. LLM tool loop, model choice, context building, cancellation |
| [TOOL_DEVELOPMENT.md](TOOL_DEVELOPMENT.md) | Tool registry API, the built-in tool catalogue, a worked "add a tool" example, adding another AI provider |
| [PERMISSIONS_AND_SECURITY.md](PERMISSIONS_AND_SECURITY.md) | Permission levels 0–5, taint escalation, path guard, terminal restrictions, approvals, secrets, redaction, IPC, CSP, sandbox |
| [GROQ_PROVIDER_MANAGER.md](GROQ_PROVIDER_MANAGER.md) | Multi-key rotation, quota scopes, header parsing, freezes, probes, circuit breaker, dashboard |
| [BROWSER_AUTOMATION.md](BROWSER_AUTOMATION.md) | Playwright on the installed Edge/Chrome, persistent profile, CDP attach, browser tools |
| [GMAIL_INTEGRATION.md](GMAIL_INTEGRATION.md) | Google OAuth setup step by step, PKCE loopback flow, email tools (drafts only, no send) |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | SQLite tables, columns, indexes, migrations, clearing data |
| [PERFORMANCE.md](PERFORMANCE.md) | Performance rules that are implemented, metrics collected, benchmark script |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Failure modes visible in the code, with causes and fixes, plus log locations |
| [TESTING.md](TESTING.md) | Running the pytest, vitest and Playwright suites, and what each file covers |
| [ROADMAP.md](ROADMAP.md) | Known gaps and not-implemented features |

Build and installer verification results are recorded separately in `IMPLEMENTATION_PROGRESS.md`, which is maintained outside this folder.

## Quick facts

| | |
|---|---|
| App version | 0.1.0 (`package.json`, `backend/core/app.py` `VERSION`) |
| App ID | `com.escan.jarvis` |
| Data folder | `%APPDATA%\Jarvis` (database, logs, models override, browser profile, screenshots) |
| Secrets | Windows Credential Manager, service name `Jarvis Desktop Agent` |
| AI provider | Groq only (chat completions + Whisper transcription) |
| Default push-to-talk shortcut | `Ctrl+Space` (`Control+Space`) |
| Wake phrase | "Hey Jarvis" (openWakeWord `hey_jarvis` model). Off by default |
