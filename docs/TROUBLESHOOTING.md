# Troubleshooting

## Where the logs are

| Log | Location | Content |
|---|---|---|
| Backend log | `%APPDATA%\Jarvis\logs\backend.log` (rotating 2 MB × 3) | Python logging, secrets redacted. Also visible in Settings → Logs & diagnostics → *Backend log* (last 200 lines) |
| Backend console | `%APPDATA%\Jarvis\logs\backend-console.log` (rotated to `.1` above 5 MB) | Raw stdout/stderr of the backend process captured by Electron, plus `[electron]` lines (spawn errors, exits with code/signal, "failed health check", "unresponsive; restarting"). **Check this first when the backend will not start** |
| Tool audit log | Settings → Logs & diagnostics → *Execution audit log* (`execution_logs` table) | Every tool request, level, decision, status, duration |
| Provider errors | Settings → Groq API keys → *Error history* (`usage` table) | Last 50 Groq errors |

Settings → Logs & diagnostics → *Open logs folder* opens the folder, and *Restart backend* restarts the Python service.

## Common problems

| Symptom | Cause (in code) | Fix |
|---|---|---|
| Banner: *"Control+Space" is already used by another application or Windows…* | `globalShortcut.register` returned false. **Ctrl+Space often collides with IME / input-language switching** (Chinese/Japanese/Korean IMEs) or another app | Settings → General → *Push-to-talk shortcut* → record another combination (e.g. `Alt+J`). The tray menu's *Activate voice* and the mic button keep working meanwhile |
| Toast: *Unsupported shortcut format. Example: Control+Space or Alt+J.* | The recorded key is not in the IPC allowlist (letters, digits, F1–F12, Space, Enter, Tab, Backquote, Insert, Home, End, PageUp, PageDown; 1–3 modifiers) | Choose a supported key. Note: the backend has already saved the rejected value, so Settings may show it while the previously registered shortcut stays active |
| Test microphone: *"Microphone returns silence. Windows may be blocking microphone access for desktop apps…"* | Every captured sample was zero, which is the usual sign of Windows privacy blocking | Windows Settings → Privacy & security → Microphone → enable *Microphone access* and *Let desktop apps access your microphone*. Then pick the right device in Settings → Voice |
| *"Could not open microphone: … Check Windows Settings → Privacy → Microphone."* | PortAudio could not open the device at 16 kHz or at its native rate | Check the device and privacy settings, or choose another input device (MME entries are listed first) |
| *"Audio subsystem unavailable"* | `sounddevice` could not load PortAudio | Reinstall (`npm run setup`) or repair the installed app |
| *"Microphone stopped delivering audio."* | No frames for 1.5 s during a session | Device unplugged or taken over; try again or switch device |
| *"I didn't catch that."* | `voice.no_speech`: no speech within `no_speech_timeout_s`, < 250 ms of speech, or Whisper returned a known hallucination | Speak sooner or louder; lower *Speech sensitivity* (VAD threshold) |
| Wake word never triggers | Disabled by default; the phrase is "Hey Jarvis" (a bare "Jarvis" may not work); tray *Pause listening* active; threshold too high | Enable it in Settings → Voice; lower *Detection threshold*; un-pause in the tray |
| *"Wake word unavailable: wake-word models are missing (run npm run setup)"* | `resources/models/wakeword/*.onnx` missing | Dev: `npm run setup`. Installed: reinstall (models are bundled from `resources/models` at build time) |
| Voice: system voice instead of Piper | Piper model missing or synthesis failed (logged "piper synthesis failed; falling back to system voice") | Run `npm run setup` (dev) or check `backend.log` |
| Banner *"backend/.venv is missing. Run `npm run setup` first."* / `npm run dev` prints *"backend/.venv not found"* | Dev mode starts `backend/.venv/Scripts/python.exe` | `npm run setup` |
| Banner *"Backend stopped unexpectedly (code N); restarting in Xs."* | The backend process exited; Electron restarts it with backoff (1, 2, 4, 8, 15 s) | Usually self-heals. Look in `backend-console.log` for the traceback |
| Banner *"The backend crashed 5 times in 5 minutes. See logs/backend-console.log."* with **Retry** | `MAX_RESTARTS = 5` within `RESTART_WINDOW_MS = 5 min` → status `failed` | Fix the cause shown in `backend-console.log` (e.g. a broken venv, a corrupt install), then click Retry (resets the counter) |
| Backend restarts by itself while running | 3 consecutive failed 20 s heartbeats (`/health` not answering in 5 s) | Look in `backend.log` for what blocked the event loop |
| Startup hangs at "Starting Jarvis services…", then a restart | `/health` not `ready` within 45 s | Check `backend-console.log`; antivirus scanning of the onedir bundle can slow the first start |
| *"Could not start the automation browser (…). Check Settings → Browser (installed Edge/Chrome channel)."* | Playwright could not launch the chosen channel: Edge/Chrome not installed, or channel `chromium` without `playwright install chromium` (not bundled) | Settings → Browser → pick an installed browser (Edge is the default); for `chromium` run `playwright install chromium` in the backend environment |
| CDP attach fails | `use_existing_chrome` on, but no Chrome listening at `cdp_url` | Start Chrome with `--remote-debugging-port=9222 --user-data-dir=<folder>` or turn the option off |
| *"Google did not return a refresh token. Remove Jarvis from your Google account permissions and try again."* | Google returned no refresh token (Jarvis already sends `access_type=offline&prompt=consent`) | Remove the app at myaccount.google.com → Security → Third-party access, then Connect Gmail again |
| *"Sign-in session expired or is invalid."* | OAuth `state` unknown or older than 10 min (e.g. the backend restarted mid-flow) | Click Connect Gmail again |
| *"Google rejected the sign-in (400/401)."* / access blocked page at Google | Wrong client type, the account is not a test user, or the scopes are not allowed | Use a **Desktop app** OAuth client and add your account as a test user ([GMAIL_INTEGRATION.md](GMAIL_INTEGRATION.md)) |
| *"Gmail authorization expired or was revoked. Reconnect it in Settings."* | Refresh failed with 400/401 | Reconnect in Settings → Gmail |
| *"That doesn't look like a Google OAuth Desktop client…"* | The client id does not end in `.apps.googleusercontent.com` | Paste the downloaded JSON file contents |
| *"No Groq API key is configured…"* | No provider rows | Settings → Groq API keys → add a key (simple "open …" commands still work) |
| *"All Groq API keys are currently unavailable; expected to recover in about …"* | Every key is ineligible: frozen scope (threshold/429/estimate), open circuit, invalid key, missing model | Settings → Groq API keys shows the reason per key and countdowns. Add a key from **another organization** (different scope) for real extra quota |
| Key shows *Invalid key* | A 401 from Groq | Replace the key, or Test after fixing it |
| Key shows *credential missing* | The Credential Manager entry was deleted outside Jarvis | Replace key |
| *"… is outside the allowed workspace folders …"* / *"Access to protected location … is not allowed."* | Path guard | Add the folder in Settings → Permissions → Workspace folders (AppData, Program Files and Windows cannot be added) |
| *"'X' is blocked: shells and system administration tools cannot be run by Jarvis."* | Terminal blocklist | By design; use a real program (git, npm, python) with arguments |
| *"'X' was not found on PATH…"* | `shutil.which` failed, or a shell built-in (dir, echo) was used | Install the tool or add it to PATH (the backend inherits Electron's environment; restart Jarvis after PATH changes) |
| *"Arguments contain characters that are unsafe for batch-file programs…"* | `.cmd`/`.bat` target (e.g. `npm.cmd`) with any of the characters ampersand, pipe, angle brackets, caret, percent, exclamation mark, double quote or a line break in the arguments | Rephrase without those characters |
| Jarvis cannot see output of a terminal I opened myself | Output inspection works only for processes Jarvis started (`run_command`/`run_project`) | Ask Jarvis to start the process with `run_project`, then use `get_process_output` |
| Approval dialog disappears / action denied | `approval_timeout_s` (default 120 s) passed → auto-deny | Respond sooner or raise the timeout in Settings → Permissions |
| Every change asks for approval even with level 3 on automatic | The task read untrusted content (web/email/file/process output), so level ≥ 3 always asks | By design ([PERMISSIONS_AND_SECURITY.md](PERMISSIONS_AND_SECURITY.md)) |
| *"I stopped after N steps without finishing…"* | `max_iterations` reached | Raise it in Settings → Desktop automation or split the request |
| SmartScreen warns when installing | Installers are not code-signed | *More info → Run anyway* (signing is on the [ROADMAP](ROADMAP.md)) |
| Start-with-Windows toggle has no effect in dev | `setLoginItemSettings` is only called when packaged | Expected in dev |

## Resetting

- Settings → Logs & diagnostics → *Clear application data* (conversations, task history, usage/quota, settings, browser profile). See [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md#clearing-data).
- Full reset: quit Jarvis (tray → Exit), delete `%APPDATA%\Jarvis`, and remove the `Jarvis Desktop Agent` entries in Windows Credential Manager.
