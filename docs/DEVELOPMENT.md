# Development

## Prerequisites

- Windows 10/11 x64
- Node.js with npm (the Electron main/preload bundles target `node22`)
- Python **3.11** reachable through the `py` launcher (`setup.mjs` runs `py -3.11 -m venv backend/.venv`)
- Microsoft Edge or Google Chrome, for browser tools
- Network access during setup (pip, and model downloads from GitHub and Hugging Face)

## One-time setup

```bash
npm install          # Node dev dependencies (Electron, Vite, React, Playwright test, …)
npm run setup        # creates backend/.venv, installs backend/requirements-dev.txt, downloads voice models
```

`npm run setup` (`scripts/setup.mjs`):
1. Creates `backend/.venv` if it is missing.
2. Upgrades pip, then installs `requirements-dev.txt` (which includes `requirements.txt`).
3. Runs `backend/scripts/fetch_models.py`, which downloads into `resources/models/`. Files that already exist are skipped, so it is safe to re-run. It exits 1 if any download failed.

| File | Source |
|---|---|
| `vad/silero_vad.onnx` | snakers4/silero-vad (GitHub raw) |
| `wakeword/melspectrogram.onnx`, `embedding_model.onnx`, `hey_jarvis_v0.1.onnx` | openWakeWord release v0.5.1 |
| `piper/en_GB-alan-medium.onnx` + `.onnx.json` | rhasspy/piper-voices (Hugging Face) |

The models are git-ignored. Without them the app still runs: VAD falls back to the energy detector, the wake word reports "models are missing", and TTS falls back to the system voice.

## npm scripts

| Script | What it does |
|---|---|
| `npm run setup` | See above |
| `npm run dev` | Starts the Vite dev server (`localhost:5173`, next free port if taken), builds main/preload with esbuild in watch mode, and launches Electron with `JARVIS_DEV_SERVER_URL`. Electron restarts automatically (300 ms debounce) when main/preload change. The renderer uses Vite HMR. Electron itself starts and supervises the Python backend from `backend/.venv` |
| `npm run build` | `build:frontend` then `build:backend` |
| `npm run build:frontend` | Vite production build → `frontend/dist`; esbuild minified main/preload → `frontend/dist-electron` (no sourcemaps in production) |
| `npm run build:backend` | PyInstaller onedir → `build/backend/jarvis-backend/jarvis-backend.exe` |
| `npm run package:win` | `build`, then electron-builder NSIS → `dist-installer/Jarvis-Setup-<version>.exe` |
| `npm run package:msi` | `build`, then electron-builder MSI → `dist-installer/Jarvis-<version>.msi` |
| `npm test` | `test:frontend` then `test:backend` |
| `npm run test:frontend` | vitest (jsdom) |
| `npm run test:backend` | `pytest tests/backend -q` with the venv Python and `PYTHONPATH=backend` |
| `npm run test:e2e` | `build:frontend`, then Playwright drives the real Electron app with the real Python backend |
| `npm run typecheck` | `tsc -p frontend/tsconfig.json --noEmit` |

See [TESTING.md](TESTING.md) and [PRODUCTION_BUILD.md](PRODUCTION_BUILD.md).

## Dev loop details

- In dev the renderer is served by Vite, so the `app://` protocol handler and its **CSP header are not in play**. CSP applies only when loading from `app://jarvis` (production builds, and e2e tests, which clear `JARVIS_DEV_SERVER_URL`).
- The allowed origins become `[<vite origin>, app://jarvis]`.
- Backend logs: `%APPDATA%\Jarvis\logs\backend.log` and `backend-console.log`. Dev and installed builds share `%APPDATA%\Jarvis`.
- Running the backend by hand: `backend/.venv/Scripts/python.exe backend/main.py --port 8765`. Without `JARVIS_AUTH_TOKEN` the token middleware is **disabled**, so only do this for local debugging.

### Backend environment variables

| Variable | Set by | Meaning |
|---|---|---|
| `JARVIS_AUTH_TOKEN` | Electron | Per-launch token; enables auth when set |
| `JARVIS_ALLOWED_ORIGINS` | Electron | Comma-separated Origin allowlist (default `app://jarvis,http://localhost:5173`) |
| `JARVIS_DATA_DIR` | Electron | Data folder (default `%APPDATA%\Jarvis`) |
| `JARVIS_RESOURCES_DIR` | Electron | Resources folder (models); default `<exe dir>/resources` when frozen, `<repo>/resources` from source |
| `JARVIS_PARENT_PID` | Electron | Backend exits when this process exits |
| `JARVIS_PORT` | optional | Default port if `--port` is omitted (8765) |
| `JARVIS_LOG_LEVEL` | optional | Default `INFO` |

Command line: `--host` (loopback only), `--port`, `--log-level`.

## Conventions

- Backend imports of heavy modules (Playwright, sounddevice, piper, pypdf, onnxruntime, keyring) are **lazy**, inside the function that needs them. Keep it that way (see [PERFORMANCE.md](PERFORMANCE.md)).
- Schema changes: append a new `(version, sql)` entry to `backend/storage/migrations.py` and never edit an applied one (see [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)).
- New settings: add the field with a default and bounds to the right model in `core/config.py`, and mirror it in `frontend/src/renderer/lib/types.ts`. Stored settings are deep-merged over the defaults, so older databases pick up new fields automatically.
- New tools: [TOOL_DEVELOPMENT.md](TOOL_DEVELOPMENT.md). New WebSocket events: emit through `services.events.emit(...)` and handle them in `renderer/lib/reducer.ts`.
- `backend/scripts/make_brand_assets.py` (Pillow, dev only) builds the brand from `resources/brand/jarvis.jpg`: the circular HUD logo (`public/brand/logo.png`), the arc-reactor mark used at small sizes (`reactor.png`, tray, 16–64 px ICO frames), the de-watermarked, vignetted background (`hero.jpg`), and `resources/icons/*`. The UI palette in `frontend/src/renderer/styles.css` (blue-black, cyan `#4fd1f5`, amber `#f0a742`, red `#e5566b`) is sampled from the same artwork.
