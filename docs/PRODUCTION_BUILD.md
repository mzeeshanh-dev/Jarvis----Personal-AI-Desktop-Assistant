# Production build

**Verification status: see IMPLEMENTATION_PROGRESS.md**

This page describes the build pipeline as configured. It does not record build results.

## Overview

```
npm run build
 ├─ build:frontend  (scripts/build-frontend.mjs)
 │   ├─ Vite (frontend/vite.config.mts, mode=production) → frontend/dist/        (renderer, base './', target chrome130)
 │   └─ esbuild (scripts/electron-build.mjs, minify, no sourcemap)
 │        main.ts → frontend/dist-electron/main.js, preload.ts → frontend/dist-electron/preload.js
 └─ build:backend   (scripts/build-backend.mjs)
     └─ PyInstaller backend/jarvis-backend.spec (--noconfirm --clean)
          → build/backend/jarvis-backend/jarvis-backend.exe (+ _internal files)
            work dir build/pyinstaller-work/
```

Then `npm run package:win` or `npm run package:msi` runs electron-builder (see [WINDOWS_MSI_PACKAGING.md](WINDOWS_MSI_PACKAGING.md)).

Before building, run `npm run setup` so that `backend/.venv` exists (`build:backend` exits with "backend/.venv not found" otherwise) and `resources/models/` is populated, because electron-builder copies that folder into the app.

## Frontend

- The renderer is a static Vite build loaded at runtime from `app://jarvis/index.html`. The protocol handler maps paths into `frontend/dist`, rejects traversal outside it (403), falls back to `index.html` for unknown paths, and adds the CSP header to HTML responses.
- esbuild bundles main and preload as CommonJS for Node 22, with `electron` external.
- Source maps: kept in dev, not generated in production. electron-builder also excludes `frontend/dist-electron/*.map`.

## Backend (PyInstaller onedir)

`backend/jarvis-backend.spec`:
- Entry `main.py`, `pathex=["."]` (it runs with `cwd=backend`).
- `hiddenimports`: all submodules of `uvicorn`, `keyring.backends` and every Jarvis package (`api`, `agent`, `tools`, `providers`, `voice`, `integrations`, `core`, `storage`), plus `win32ctypes.core` and `pypdf`. Many imports are lazy, so PyInstaller's static analysis would otherwise miss them.
- `datas`: `collect_data_files("piper")`, which provides the espeak-ng data used by Piper's phonemizer.
- `excludes`: tkinter, matplotlib, IPython, pytest, PIL, scipy, pandas.
- `console=True`: stdio is piped to Electron, and Electron spawns it with `windowsHide`, so no console window appears.
- Icon `../resources/icons/icon.ico`.
- **onedir** (`COLLECT`): fast startup and no temp extraction on each launch.

The script checks that `build/backend/jarvis-backend/jarvis-backend.exe` exists and fails otherwise.

## What the packaged app contains

| Packaged path | Source |
|---|---|
| `resources/app.asar` | `frontend/dist/**`, `frontend/dist-electron/**` (no `.map`), `package.json` |
| `resources/backend/` | `build/backend/jarvis-backend/` |
| `resources/models/` | `resources/models/` |
| `resources/icons/` | `resources/icons/` |

At runtime Electron starts `resources/backend/jarvis-backend.exe --port N` with `cwd` set to that folder and `JARVIS_RESOURCES_DIR=process.resourcesPath`. `npmRebuild: false` because there are no native Node modules.

## Not bundled

- The Playwright **Chromium** browser. The default channels `msedge` and `chrome` use the browsers installed on Windows. The `chromium` channel needs `playwright install chromium`, which is not part of the build.
- Code signing (not configured).
