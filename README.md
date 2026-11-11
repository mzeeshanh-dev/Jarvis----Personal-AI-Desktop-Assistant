# Jarvis

A local-first desktop AI agent for Windows 11. Press **Ctrl + Space** anywhere, speak, and Jarvis opens apps, searches the web, drives a browser, finds and reads files (including PDFs), inspects projects, runs approved commands, and works with Gmail. Everything runs from one desktop app: Electron starts and supervises the Python backend itself.

```
Electron (React UI, tray, global shortcut)  --spawns & supervises-->  Python backend (FastAPI)
        |  app:// + CSP, sandboxed renderer          127.0.0.1:<random port> + per-launch token
        +------------- REST + WebSocket ---------------+
                                                       |-- agent (planner -> LLM tool loop -> executor)
                                                       |-- tools: apps, browser (Playwright), files, terminal, Gmail
                                                       |-- voice: mic -> Silero VAD -> denoise -> Groq Whisper; Piper TTS; openWakeWord
                                                       |-- Groq provider manager (multi-key, quota scopes, 97% freeze)
                                                       +-- SQLite (history, logs, settings, usage)
```

## Quick start (development)

Requirements: Windows 10/11, Node.js 20+, Python 3.11.

```powershell
npm install
npm run setup        # creates backend/.venv, installs Python deps, downloads voice models
npm run dev          # one command: Vite + Electron; Electron starts the backend
```

Then open **Settings → Groq API keys** and add a key from <https://console.groq.com/keys>.

| Command | What it does |
|---|---|
| `npm run dev` | Full development environment (renderer HMR, main/preload rebuild, backend auto-start) |
| `npm run shortcut` | Desktop/Start-menu shortcut that runs Jarvis via the known Electron/Python runtimes (works with Smart App Control on) |
| `npm run build` | Production build of the frontend and the PyInstaller backend |
| `npm run package:win` | Windows NSIS installer → `dist-installer/Jarvis-Setup-<version>.exe` |
| `npm run package:msi` | Windows MSI → `dist-installer/Jarvis-<version>.msi` |
| `npm run test` | Frontend (Vitest) and backend (pytest) unit/integration tests |
| `npm run test:e2e` | Electron end-to-end tests (Playwright), real backend |

The installed app needs neither Python nor Node.js and no terminal. It installs per-user without administrator rights, and your data in `%APPDATA%\Jarvis` survives upgrades.

## Documentation

Everything is in [`docs/`](docs/README.md): architecture, voice pipeline, agent orchestration, tool development, permissions and security, the Groq provider manager, Gmail setup, the database schema, performance, packaging, troubleshooting and testing. Current status, measured numbers and known limitations are in [`docs/IMPLEMENTATION_PROGRESS.md`](docs/IMPLEMENTATION_PROGRESS.md).
