# Permissions and security

The LLM can only **request** tools. The backend validates every argument, assesses the real risk, applies the user's policy, and asks the user when the policy requires it. All of this happens in `agent/executor.py` and `agent/permissions.py`. Content read from the web, email, files or process output cannot grant itself permissions.

## Permission levels

| Level | Name (`LEVEL_NAMES`) | Default policy | Examples |
|---|---|---|---|
| 0 | Read-only | automatic | list apps, read web pages, search/read email, inspect process |
| 1 | Open apps & websites | automatic | open_application, open_url, browser_open/navigate/search, stop a Jarvis-started process |
| 2 | Read local files | automatic (path guard restricts it to the workspace) | search_files, list_directory, read_file |
| 3 | Create & modify | `level3_policy`: **ask** (default) or auto | create/edit/move/copy file, close_application, browser_click/fill, create_email_draft |
| 4 | Run commands | ask; allowlisted `run_command` may be automatic | run_command, run_project, stop_process on a foreign PID |
| 5 | Irreversible | **always ask**, cannot be set to automatic | delete_file |

## `evaluate()` rules, in order

`permissions.evaluate(tool_name, level, settings, auto_ok, tainted)` returns ALLOW, ASK or DENY:

1. Per-tool override `deny` → **DENY** ("This tool is disabled in Settings → Permissions.").
2. `level >= 5` → **ASK**, always, overriding any `auto` override.
3. Per-tool override `ask` → **ASK**.
4. **Taint escalation**: `tainted and level >= 3` → **ASK** ("This task read untrusted web/email/file content; confirming before changes."). This comes *before* the `auto` override and the level-3 policy, so neither can bypass it.
5. Per-tool override `auto` → **ALLOW**. This applies to level 3 and 4 tools too, including non-allowlisted commands.
6. `level <= 2` → **ALLOW**.
7. `level == 3` → ALLOW if `level3_policy == "auto"`, else **ASK**.
8. `level == 4 and auto_ok and allowlisted_commands_auto` → **ALLOW** ("Command is on the allowlist.").
9. Otherwise → **ASK** ("Runs a command on your computer.").

When the decision is not ALLOW, the assessment's reason (for example "Command looks like it installs, removes or publishes something.") is appended.

**Taint** (`ctx.tainted`) is set for the rest of a task once any tool returns a successful result with `untrusted=True`: `browser_search`, `browser_read_page`, `browser_extract_content`, `read_file`, `run_command`, `run_project`, `get_process_output`, and all email reads/summaries. Untrusted results also reach the model wrapped in `<untrusted_content>` with an instruction not to follow them, and the system prompt repeats that rule.

Per-tool overrides (`permissions.tool_overrides`, values `auto`/`ask`/`deny`) are edited in Settings → Permissions. The UI disables "Automatic" for level 5 tools, and the backend ignores it anyway (rule 2).

## Approval broker (`ApprovalBroker`)

- `request()` creates a pending approval with a 12-hex id and emits `approval.request` with `{id, task_id, tool, level, level_name, reason, summary, arguments, timeout_s}`. The arguments are redacted and truncated.
- It waits on a future for **`approval_timeout_s`** (default **120 s**, allowed 10–900; the UI slider covers 15–600). On timeout it emits `approval.expired` and returns **False**: unanswered requests are denied.
- The user answers with WS `approval.respond {id, approved}` or `POST /api/approvals/{id}`. The dialog shows a countdown, queues multiple requests, and denies on Escape.
- Cancelling a task cancels its wait. Approvals still pending in `hello` are re-sent to reconnecting windows.
- Declines are logged as `decision=declined` and the tool returns `code="declined"`. On the direct route, a decline or deny becomes the reply text without falling back to the LLM.

## Path guard (`tools/pathguard.py`)

Every filesystem path and every command `cwd` goes through `pathguard.resolve()`:

1. Empty → `invalid_path`. UNC paths (`\\…`, `//…`) are refused.
2. `~` and `%VARS%` are expanded. A relative path is resolved against the **first** workspace root.
3. The path is resolved with `os.path.realpath`, which follows symlinks, junctions and `..`.
4. **Protected roots** are always denied, even if configured as a workspace: `%SystemRoot%`, `%ProgramFiles%`, `%ProgramFiles(x86)%`, `%ProgramData%`, `%APPDATA%`, `%LOCALAPPDATA%`, `<SystemDrive>\$Recycle.Bin`, `\System Volume Information`, `\Recovery`, and Jarvis's own data folder.
5. The path must be inside a configured **workspace root** (defaults: `~/Desktop`, `~/Documents`, `~/Downloads`). No roots configured → denied.
6. **Sensitive files** are denied:
   - names: `.env`, `.env.local`, `.env.production`, any `.env.*`, `id_rsa/id_dsa/id_ecdsa/id_ed25519`, `credentials(.json)`, `login data`, `cookies`, `web data`, `key4.db`, `logins.json`, `.npmrc`, `.pypirc`, `.netrc`, `_netrc`, `.git-credentials`, `token.json`, `client_secret.json`, `ntuser.dat`;
   - suffixes: `.pem .key .pfx .p12 .kdbx .ovpn .ppk .keystore .jks`;
   - any path inside `.ssh .aws .gnupg .azure .kube .docker .gcloud microsoft\credentials microsoft\protect`.
7. `must_exist` → `not_found` if the path is missing.

`search_files` additionally skips sensitive files, hidden directories (names starting with `.`), and `node_modules, .git, __pycache__, .venv, venv, .mypy_cache, .pytest_cache, .next, .cache, $recycle.bin, appdata, .idea, .gradle, site-packages`. `delete_file` refuses a workspace root itself and moves targets to the Recycle Bin.

## Terminal restrictions (`tools/terminal.py`)

- **No shell.** The model supplies an `executable` and an `args` list, which go to `asyncio.create_subprocess_exec`. Nothing is interpreted by cmd.exe or PowerShell.
- **Blocked executables** (checked against the requested name and against the resolved path, with `.exe/.cmd/.bat` removed): `cmd, powershell, pwsh, powershell_ise, bash, sh, wsl, wscript, cscript, mshta, regedit, reg, rundll32, regsvr32, bcdedit, diskpart, format, shutdown, schtasks, sc, net, net1, netsh, takeown, icacls, cacls, cipher, vssadmin, wmic, certutil, bitsadmin, msiexec, runas, psexec, sdelete, attrib, del, erase, rd, rmdir, mklink, wevtutil, fsutil, setx`.
- The executable must resolve on `PATH` (`shutil.which`). Shell built-ins such as `dir`, `echo` and `cd` are therefore unsupported.
- **Batch metacharacter check**: if the resolved program is a `.cmd`/`.bat` file (for example `npm.cmd`), Windows runs it through cmd.exe, so any argument containing `& | < > ^ % ! "`, CR or LF is refused.
- **Allowlist** (`permissions.command_allowlist`): `<exe stem> <args>` (lower-cased) must equal an entry or start with an entry followed by a space. Defaults: `git status, git log, git diff, git branch, git show, node --version, npm --version, python --version, npm ls, pip list, npm test, npm run test, npm run lint, pytest`. An allowlisted command is `auto_ok` only if it is `run_command` (never `run_project`) and its arguments do not match the **RISKY** pattern (`install, uninstall, add, remove, publish, push, deploy, reset, clean, prune, rm, delete, drop, force, upgrade, update, migrate`, or `-f/--force/-rf`). Automatic runs also require `allowlisted_commands_auto` (default on).
- `cwd` must be an existing folder inside the workspace; the default is the first workspace root.
- Process environment: `JARVIS_AUTH_TOKEN` is removed. `PYTHONUNBUFFERED=1`, `FORCE_COLOR=0` and `NO_COLOR=1` are set. Flags: `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`, stdin `DEVNULL`, stderr merged into stdout.
- Output is captured line by line, **redacted**, and capped at `max_output_kb` (default 200 KB, oldest lines dropped). The manager keeps the last 20 finished processes.
- Timeouts: `run_command` waits `min(timeout_s argument, automation.command_timeout_s)` (default 120 s), then kills the process tree (`taskkill /T /F`, psutil fallback). Cancellation also kills the tree. All managed processes are killed on backend shutdown.
- Process **output is available only for processes Jarvis started** (`run_command`, `run_project`). Jarvis cannot read the output of arbitrary existing terminals. `inspect_process` can report status, CPU, memory and the redacted command line of any PID.
- `stop_process`/`close_application` refuse protected processes: `system, idle, csrss, wininit, winlogon, lsass, services, smss, svchost, dwm, fontdrvhost, registry, memory compression, jarvis.exe, jarvis-backend.exe, electron.exe, msmpeng, securityhealthservice`. `stop_process` also refuses Jarvis's own PID and its parent's.

## Other tool guards

- `open_url`: http, https and mailto only. `browser_*`: http and https only.
- `browser_fill` refuses `type=password` fields. The automation browser uses a dedicated profile, and attaching to a real Chrome over CDP is opt-in ([BROWSER_AUTOMATION.md](BROWSER_AUTOMATION.md)).
- Email: drafts only, and recipient addresses are validated. **There is no send tool.**

## Secret storage (`core/secrets.py`)

- `SecretStore` uses `keyring` with **`WinVaultKeyring` pinned explicitly** on Windows (Windows Credential Manager), under the service name `Jarvis Desktop Agent`. Entry names: `groq:<provider id>`, `gmail:client_id`, `gmail:client_secret`, `gmail:refresh_token`.
- SQLite stores only the reference (`providers.credential_ref`) and a hint (`key_hint`, the first 4 and last 4 characters, or `…` for short keys).
- API responses never include secrets (the provider endpoints return `key_hint` only). The renderer never sees them, and tests assert that secrets never reach the database.
- Gmail access tokens are kept in memory only.

## Redaction

`redact()` replaces these patterns with `[REDACTED]`: `gsk_…` (Groq), `Bearer <token>`, `ya29.…` (Google access tokens), `1//…` (Google refresh tokens), and `api_key / client_secret / refresh_token / access_token / password` followed by `:` or `=` and a value. It is applied to:
- every log record (`RedactingFilter` on file and console handlers);
- provider error text stored in `usage.error` and `providers.last_error`;
- audit-log arguments, errors and previews, and the tool arguments sent to the UI;
- captured process output and `inspect_process` command lines;
- tool crash messages.

## Backend API protection

See [ARCHITECTURE.md](ARCHITECTURE.md#authentication). In short: loopback-only bind, a per-launch 256-bit token compared in constant time, an Origin allowlist, WS close code 4401, a random port, no OpenAPI or docs routes, and Pydantic models with size limits on every input.

## Electron hardening

- **Sandboxed renderers**: `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`, `webSecurity: true`. The e2e test asserts that `window.require` and `window.process` are undefined.
- **All device permissions denied**: `setPermissionRequestHandler(() => cb(false))`, `setPermissionCheckHandler(() => false)`.
- **Navigation**: `will-navigate` is blocked unless the target is the app origin (or the dev URL). `window.open` is denied; http(s) URLs are handed to the OS browser.
- **CSP** on every HTML response from `app://`:
  `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src http://127.0.0.1:* ws://127.0.0.1:*; media-src 'self' http://127.0.0.1:* blob:; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'`. The CSP is not applied when the renderer is served by the Vite dev server.
- **app:// handler**: paths are normalised and must stay inside `frontend/dist` (403 otherwise).
- **Preload bridge** (`window.jarvis`): exposes only named `invoke` wrappers and an `on()` limited to `backend:status`, `navigate` and `shortcut:status`. No Node or OS primitives.
- **IPC validation** (`main.ts`): every handler checks that the sender frame URL starts with `app://jarvis/` (or the dev URL), else "untrusted sender".
  - `desktop:apply`: object only; the shortcut must match `^((Control|Ctrl|Alt|Shift|Super|CommandOrControl)\+){1,3}([A-Z0-9]|F1–F12|Space|Enter|Tab|Backquote|Insert|Home|End|PageUp|PageDown)$`; only known boolean keys are accepted.
  - `shell:open-external`: string, ≤ 4000 chars, `http://`, `https://` or `mailto:` only (opened in the default browser).
  - `shell:open-folder`: only `logs`, `data` or `screenshots`.
  - `window:show-main`: the route must be a string starting with `/`.
- **Markdown** rendering in the chat (`renderer/lib/markdown.tsx`) builds React elements and never injects HTML. Links go through a click handler to `openExternal` (tested in `tests/frontend/markdown.test.tsx`).
- **Single instance** and `backgroundThrottling: false` (so playback keeps working while hidden).

## Data protection notes

- The automation browser profile (`browser-profile`) holds cookies and logins for sites the user signs into there. It can be wiped via Settings → Logs & diagnostics → Clear data → *Automation browser profile*.
- No telemetry. Outbound network calls go to Groq (`api.groq.com`), Google OAuth/Gmail (`accounts.google.com`, `oauth2.googleapis.com`, `gmail.googleapis.com`) and whatever sites the browser tools open. `npm run setup` also downloads models from GitHub and Hugging Face.
