# Installation and configuration (owner guide)

> The installers are defined in `installer/electron-builder.yml`. This page describes what that configuration does. It does not claim that a specific installer was built or tested; see `IMPLEMENTATION_PROGRESS.md` for verification results.

## Requirements

- Windows 10/11 (x64). The backend relies on Windows APIs throughout: Credential Manager, `taskkill`, `SHFileOperationW`, `os.startfile` and the Start Menu index.
- **Microsoft Edge** (default) or **Google Chrome** installed, for browser automation.
- A Groq account and at least one API key, for the AI agent and voice transcription. Simple commands ("open Chrome", "search Google for …") work without a key.
- Optional: a Google account plus a Google Cloud OAuth client, for Gmail.
- A microphone, for voice.

## Install

Two installer types can be produced (see [WINDOWS_MSI_PACKAGING.md](WINDOWS_MSI_PACKAGING.md)):

| File | Type | Notes |
|---|---|---|
| `Jarvis-Setup-<version>.exe` | NSIS, assisted (not one-click) | Per-user by default, so **no admin rights needed**. The install-mode page also offers an all-users install, which elevates. You can change the install directory. Creates desktop and Start Menu shortcuts ("Jarvis"). Runs Jarvis when setup finishes. |
| `Jarvis-<version>.msi` | MSI (WiX, via electron-builder) | Per-user (`perMachine: false`), one-click. Desktop and Start Menu shortcuts. |

The installers are **not code-signed** (no signing is configured), so Windows SmartScreen may warn on first run. See [ROADMAP.md](ROADMAP.md).

## Where things live

| What | Where |
|---|---|
| Program files | The install directory chosen in the installer. It contains `Jarvis.exe` and `resources\backend\jarvis-backend.exe`, `resources\models\`, `resources\icons\` |
| Settings, history, logs, browser profile | `%APPDATA%\Jarvis` |
| Groq keys, Gmail client and refresh token | Windows Credential Manager → Windows Credentials → generic entries for `Jarvis Desktop Agent` |

**Uninstall and upgrade keep your data.** `deleteAppDataOnUninstall: false` means `%APPDATA%\Jarvis` survives uninstalls, reinstalls and upgrades. Credential Manager entries are not removed by the uninstaller either. To remove everything, delete the keys in Settings → Groq API keys, remove the Gmail client in Settings → Gmail, then uninstall and delete `%APPDATA%\Jarvis`.

## First run

1. Jarvis opens the main window and starts its background service. The banner reads "Starting Jarvis services…" until it is ready.
2. The tray icon appears. Closing the window keeps Jarvis running in the tray (Settings → General → "Closing the window keeps Jarvis running in the tray"). To quit, use **Tray → Exit Jarvis**.

### 1. Add a Groq API key

1. Create a key at `console.groq.com/keys`.
2. Settings → **Groq API keys** → *Add a Groq API key*: enter a label and the `gsk_…` key, and choose a **quota scope**.
3. Click **Add & test**. Jarvis stores the key in Credential Manager and immediately calls `GET /models` to validate it.

Quota scope rule: keys from the **same Groq organization share one quota**, so give them the same scope (for example `default`). Extra keys in one organization add failover, **not** extra quota. Use a different scope only for keys from a different organization. Details: [GROQ_PROVIDER_MANAGER.md](GROQ_PROVIDER_MANAGER.md).

Optional: Settings → **AI providers** lets you choose the models for simple/complex/summarization/transcription and enter local limits (RPM, TPD, ASH, ASD) copied from the Groq console. Those limits feed the local quota estimates.

Defaults (verified against Groq's `/models` on 2026-11-04): simple `openai/gpt-oss-20b`, complex `openai/gpt-oss-120b`, summarization `openai/gpt-oss-120b`, transcription `whisper-large-v3-turbo`. If Groq retires a configured model, Jarvis automatically uses the best model your keys still offer.

To add a key from a file without the UI (Jarvis closed): `backend\.venv\Scripts\python.exe backend\scripts\seed_groq_key.py <path-to-key-file> --label Primary`. The key goes to Windows Credential Manager and is never printed.

### 2. Set workspace folders

File and command tools only work inside the **workspace folders** (Settings → Permissions). The defaults are your `Desktop`, `Documents` and `Downloads`. Add project folders here. Windows, Program Files, AppData, Jarvis's own data and credential files are always blocked. See [PERMISSIONS_AND_SECURITY.md](PERMISSIONS_AND_SECURITY.md).

### 3. Voice

- Push-to-talk: press **Ctrl+Space** anywhere, speak, and pause. Jarvis stops listening by itself after 0.9 s of silence, or you can press the shortcut again. Change the shortcut in Settings → General. Ctrl+Space can conflict with IME switching; see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
- Settings → Voice → **Test microphone** checks the device and the Windows microphone privacy permission.
- Wake word (off by default): enable it in Settings → Voice. Say **"Hey Jarvis"**, wait for "Yes?", then speak. A bare "Jarvis" may or may not trigger it. While the wake word is enabled the microphone stays open. Tray → *Pause listening* suspends it.
- Spoken replies: Piper (local neural voice, `en_GB-alan-medium`) when the model is present, otherwise the Windows system voice. This can be switched off.

### 4. Gmail (optional)

Follow [GMAIL_INTEGRATION.md](GMAIL_INTEGRATION.md#setup-step-by-step). Jarvis can search and read email and **create drafts**. It has **no tool to send email**.

### 5. Start with Windows

Settings → General → *Start Jarvis when I sign in to Windows*. This registers a login item with the `--hidden` argument, so Jarvis starts in the tray without showing the window. It only applies to the installed app; in dev runs (`app.isPackaged === false`) the toggle is saved but no login item is registered.

## Upgrading

Install the newer installer of the same type over the existing one:
- NSIS upgrades the installation identified by the app ID `com.escan.jarvis`.
- MSI uses a constant `upgradeCode`, which gives a major upgrade (see [WINDOWS_MSI_PACKAGING.md](WINDOWS_MSI_PACKAGING.md)).

In both cases `%APPDATA%\Jarvis` and the Credential Manager entries are kept. Database migrations run automatically at startup.

## Launcher mode (recommended while Smart App Control is on)

Windows Smart App Control blocks unsigned executables that have no reputation, and it has no per-app allowlist. Until the installer is code-signed, run Jarvis from the project folder through known runtimes instead of the unsigned `Jarvis.exe`:

```powershell
cd jarvis
npm install          # once
npm run setup        # once: Python venv, dependencies, voice models
npm run shortcut     # builds the UI and creates Desktop + Start-menu "Jarvis" shortcuts
```

The shortcut runs `node_modules\electron\dist\electron.exe "<project folder>"`. Electron then starts the backend with `backend\.venv\Scripts\python.exe` (signed by the Python Software Foundation). The behavior is identical to the installed app: tray, Ctrl+Space, the backend starts and stops automatically, and data lives in `%APPDATA%\Jarvis`. Settings → General → "Start Jarvis when I sign in" also works in this mode. Keep the project folder where it is, and re-run `npm run shortcut` after pulling changes to rebuild the UI. To quit, use the tray icon → Exit Jarvis, or run `node_modules\electron\dist\electron.exe . --quit`.

## Stopping Jarvis

Closing or minimizing the window only hides Jarvis in the tray; it keeps listening for Ctrl+Space and the wake word. To stop it completely (microphone released, tasks cancelled, backend closed):

| Way | What happens |
|---|---|
| Sidebar → **Shut down Jarvis** → *Shut down* | Graceful: cancels tasks, releases the mic, stops the backend, exits |
| Sidebar → Shut down Jarvis → **Emergency stop** | Immediate: kills the backend and every process it started, then exits |
| **Ctrl+Alt+Shift+X** (works while hidden) | Emergency stop |
| Tray icon → Exit Jarvis / Emergency stop | Same as above |
| Click the Jarvis shortcut while it runs | Asks *Open Jarvis / Shut down Jarvis / Cancel* |
| `Jarvis.exe --quit` (launcher: `electron.exe <project> --quit`) | Graceful shutdown from a script |

Jarvis never asks for or needs administrator rights. Running an assistant that can execute commands as admin would widen what a mistake or a prompt injection could do.
