# Browser automation

Code: `backend/tools/browser.py` (`BrowserManager` + 9 tools). Settings: `BrowserSettings` in `core/config.py`, UI Settings → Browser.

## Design

- **Playwright (Python, async API)** drives a Chromium-family browser. The import is lazy: Playwright loads on the first browser tool call, not at startup.
- **The installed Edge or Chrome is used through Playwright's `channel`** (`msedge` default, or `chrome`), so no browser is downloaded and the installer stays small.
- **Dedicated persistent profile** at `%APPDATA%\Jarvis\browser-profile` (`launch_persistent_context`). The user's normal browser profile, cookies and saved passwords are never touched. Logins made inside the automation browser persist in that profile.
- **One browser context per backend.** `BrowserManager.ensure()` starts it on first use under a lock and reuses the current page (or the last open page, or a new one) for later calls. `browser_open` with a URL opens a new tab.
- Launch options: `headless` (setting, default false), `no_viewport=True`, `accept_downloads=False`.
- If the context closes (for example the user closes the window), the manager resets its state and the next tool call starts a new context.
- The browser is closed on backend shutdown, by `browser_close`, when browser settings change while it is running, and before *Clear data → Automation browser profile* deletes the profile folder.

## Settings

| Setting | Default | Notes |
|---|---|---|
| `channel` | `msedge` | `msedge`, `chrome`, or `chromium`. **`chromium` requires `playwright install chromium`, which is not bundled**; with `chromium` no `channel` is passed to Playwright |
| `headless` | `false` | |
| `use_existing_chrome` | `false` | Attach over CDP instead of launching (see below) |
| `cdp_url` | `http://127.0.0.1:9222` | |
| `search_engine` | `google` | `google`, `bing`, `duckduckgo`; used by `browser_search` and by the direct "search …" route |

### Attaching to an existing Chrome (opt-in)

With `use_existing_chrome` on, the manager calls `chromium.connect_over_cdp(cdp_url, timeout=8000)` and uses the first existing context (or a new one). Start Chrome yourself with `chrome.exe --remote-debugging-port=9222 --user-data-dir=<separate folder>`. This gives Jarvis access to every site logged in in that Chrome, which is why it is off by default. On close Jarvis only disconnects, and your Chrome keeps running.

## Tools

| Tool | Level | Behaviour |
|---|---|---|
| `browser_open` | 1 | Starts the browser; with `url`, opens a new tab and navigates. Returns URL and title |
| `browser_search` | 1 | Navigates to the engine's results page (DuckDuckGo uses `duckduckgo.com/html/`), waits up to 8 s for `load`, extracts up to `max_results` (1–20, default 8) results `{title, url, snippet}` from `a h3`, `h2 a` and `a.result__a`. **Untrusted** |
| `browser_navigate` | 1 | `page.goto(url, wait_until="domcontentloaded", timeout=30 s)` |
| `browser_read_page` | 0 | `innerText` of `main`, `article`, `[role=main]` or `body`, whitespace-collapsed, `max_chars` 500–60000 (default 8000). **Untrusted** |
| `browser_extract_content` | 0 | `kind` = links / headings / tables / forms / text, optional CSS `selector` scope, `max_items` ≤ 300 (tables: ≤ 10 tables × 50 rows). **Untrusted** |
| `browser_click` | 3 | Locator: `role` (+ `name`, `exact`), visible `text`, or CSS `selector` (last resort); clicks the first match with an 8 s timeout, then waits for `domcontentloaded` |
| `browser_fill` | 3 | Locator: `label`, `placeholder`, or `selector`; **refuses password fields**; optional `submit` presses Enter |
| `browser_screenshot` | 0 | PNG to `%APPDATA%\Jarvis\screenshots\page-<timestamp>.png` (optional `full_page`) |
| `browser_close` | 1 | Closes the context (or disconnects CDP) and stops Playwright |

URL validation: `browser_open`/`browser_navigate` accept only `http(s)` URLs with a host. A missing scheme is prefixed with `https://`.

Errors:
- A browser that cannot start → `ToolError(code="browser_unavailable")`: "Could not start the automation browser (…). Check Settings → Browser (installed Edge/Chrome channel)."
- A failed navigation → `ToolError(code="navigation", transient=True)`.
- A failed click or fill → `code="no_match"`.

Because page text and search results are marked untrusted, any level ≥ 3 action later in the same task (clicks, fills, file writes, drafts, commands) requires approval regardless of policy. See [PERMISSIONS_AND_SECURITY.md](PERMISSIONS_AND_SECURITY.md).

## Not the same as `open_url`

`open_url` (and the direct route for "open youtube" or "search Google for …") opens the URL in the user's **default browser** via `os.startfile`. It does not use Playwright and cannot read the page. The `browser_*` tools use the separate automation browser.
