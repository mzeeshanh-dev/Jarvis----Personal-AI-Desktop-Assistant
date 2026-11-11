// Electron main process: single instance, windows, tray, global shortcut,
// secure app:// protocol, validated IPC, and backend supervision.
import {
  app, BrowserWindow, dialog, globalShortcut, ipcMain, Menu, nativeImage, net as enet, protocol, screen, session, shell, Tray,
  type IpcMainInvokeEvent,
} from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { BackendManager } from './backend';

// Empty string counts as unset (otherwise `??` below would load a blank URL).
const DEV_URL = process.env.JARVIS_DEV_SERVER_URL || undefined;
const ROOT = path.resolve(__dirname, '..', '..'); // <root>/frontend/dist-electron -> <root>
const RENDERER_DIR = path.join(ROOT, 'frontend', 'dist');
const APP_ORIGIN = 'app://jarvis';
const ICON_DIR = app.isPackaged ? path.join(process.resourcesPath, 'icons') : path.join(ROOT, 'resources', 'icons');

interface DesktopSettings {
  shortcut: string;
  close_to_tray: boolean;
  float_always_on_top: boolean;
  show_float_on_voice: boolean;
  launch_at_login: boolean;
  minimize_to_tray: boolean;
}

let desktop: DesktopSettings = {
  shortcut: 'Control+Space', close_to_tray: true, float_always_on_top: true, show_float_on_voice: true, launch_at_login: false,
  minimize_to_tray: true,
};
let trayHintShown = false;
let restoringUntil = 0;
let shuttingDown = false;
let askingOnRelaunch = false;
const EMERGENCY_SHORTCUT = 'Control+Alt+Shift+X';
let mainWindow: BrowserWindow | null = null;
let floatWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let quitting = false;
let registeredShortcut: string | null = null;
let shortcutError: string | null = null;
let wakePaused = false;

// Windows groups windows/notifications by this ID and takes the display name
// and icon from a Start-menu shortcut carrying the same ID.
const APP_ID = 'com.escan.jarvis';
app.setAppUserModelId(APP_ID);

/** Launcher mode: stamp our ID onto the Jarvis shortcuts so notifications say
 *  "Jarvis" (with the icon) instead of the raw ID. The installer does this itself. */
function stampLauncherShortcuts() {
  if (app.isPackaged || DEV_URL || process.platform !== 'win32') return;
  const links = [
    path.join(app.getPath('appData'), 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Jarvis.lnk'),
    path.join(app.getPath('desktop'), 'Jarvis.lnk'),
  ];
  for (const link of links) {
    try {
      if (!fs.existsSync(link)) continue;
      const cur = shell.readShortcutLink(link);
      if (path.resolve(cur.target) !== path.resolve(process.execPath) || cur.appUserModelId === APP_ID) continue;
      shell.writeShortcutLink(link, 'update', { ...cur, appUserModelId: APP_ID });
    } catch { /* best effort */ }
  }
}
const gotLock = app.requestSingleInstanceLock();
// Second instance: hand over to the running one (see 'second-instance').
// `--quit` with no running instance has nothing to do either.
if (!gotLock || process.argv.includes('--quit')) {
  app.quit();
  process.exit(0);
}

protocol.registerSchemesAsPrivileged([
  { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } },
]);

const origins = DEV_URL ? [new URL(DEV_URL).origin, APP_ORIGIN] : [APP_ORIGIN];
const backend = new BackendManager({ allowedOrigins: origins, root: ROOT });

function rendererUrl(hash = ''): string {
  return (DEV_URL ?? `${APP_ORIGIN}/index.html`) + (hash ? `#${hash}` : '');
}

function icon(name: string) {
  const p = path.join(ICON_DIR, name);
  return fs.existsSync(p) ? nativeImage.createFromPath(p) : nativeImage.createEmpty();
}

function secureWebPreferences() {
  return {
    preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true,
    sandbox: true,
    nodeIntegration: false,
    webSecurity: true,
    spellcheck: false,
    backgroundThrottling: false, // keep TTS playback / events flowing when hidden
  };
}

function hardenContents(win: BrowserWindow) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(DEV_URL ?? APP_ORIGIN)) e.preventDefault();
  });
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1200, height: 800, minWidth: 920, minHeight: 600, show: false,
    title: 'Jarvis', backgroundColor: '#04070a', icon: icon('icon.png'),
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#04070a', symbolColor: '#89a3b3', height: 38 },
    webPreferences: secureWebPreferences(),
  });
  hardenContents(mainWindow);
  void mainWindow.loadURL(rendererUrl());
  mainWindow.once('ready-to-show', () => {
    if (!process.argv.includes('--hidden')) mainWindow?.show();
  });
  mainWindow.on('close', (e) => {
    if (!quitting && desktop.close_to_tray) {
      e.preventDefault();
      hideToTray();
    }
  });
  // The backend (shortcut, wake word, tasks) keeps running while hidden.
  mainWindow.on('minimize', () => {
    if (desktop.minimize_to_tray && Date.now() > restoringUntil) hideToTray();
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

function hideToTray() {
  mainWindow?.hide();
  if (!trayHintShown && tray) {
    trayHintShown = true;
    const key = (registeredShortcut ?? desktop.shortcut).replace('Control', 'Ctrl');
    tray.displayBalloon({
      title: 'Jarvis is still running',
      content: `Press ${key} or say "Hey Jarvis" (if enabled). Click the tray icon to open Jarvis.`,
      iconType: 'custom', icon: icon('icon.png'),
    });
  }
}

function createFloatWindow() {
  const { workArea } = screen.getPrimaryDisplay();
  const width = 440, height = 148;
  floatWindow = new BrowserWindow({
    width, height, x: Math.round(workArea.x + (workArea.width - width) / 2), y: workArea.y + workArea.height - height - 24,
    frame: false, transparent: true, resizable: false, maximizable: false, minimizable: false, fullscreenable: false,
    skipTaskbar: true, show: false, alwaysOnTop: desktop.float_always_on_top, hasShadow: false,
    backgroundColor: '#00000000', webPreferences: secureWebPreferences(),
  });
  floatWindow.setAlwaysOnTop(desktop.float_always_on_top, 'floating');
  hardenContents(floatWindow);
  void floatWindow.loadURL(rendererUrl('/float'));
  floatWindow.on('closed', () => { floatWindow = null; });
}

function showMain(route?: string) {
  if (!mainWindow) createMainWindow();
  // Restore before showing: show() on a window that was minimized-then-hidden
  // re-shows it minimized, which would fire 'minimize' and hide it again.
  restoringUntil = Date.now() + 1000;
  if (mainWindow!.isMinimized()) mainWindow!.restore();
  mainWindow!.show();
  mainWindow!.focus();
  if (route) mainWindow!.webContents.send('navigate', route);
}

function showFloat() {
  if (!floatWindow) createFloatWindow();
  floatWindow!.showInactive();
}

function broadcast(channel: string, payload: unknown) {
  for (const w of [mainWindow, floatWindow]) if (w && !w.isDestroyed()) w.webContents.send(channel, payload);
}

async function backendFetch(pathname: string, init: RequestInit = {}) {
  const info = backend.info();
  if (!info.url) throw new Error('backend not ready');
  return fetch(info.url + pathname, {
    ...init,
    headers: { 'Content-Type': 'application/json', 'X-Jarvis-Token': info.token!, ...(init.headers ?? {}) },
    signal: AbortSignal.timeout(8000),
  });
}

async function toggleVoice() {
  try {
    await backendFetch('/api/voice/toggle', { method: 'POST', body: '{}' });
    const mainFocused = mainWindow?.isVisible() && mainWindow.isFocused();
    if (desktop.show_float_on_voice && !mainFocused) showFloat();
  } catch {
    showMain();
  }
}

function registerShortcut(accel: string): { ok: boolean; error?: string } {
  if (registeredShortcut) globalShortcut.unregister(registeredShortcut);
  registeredShortcut = null;
  let ok = false;
  try {
    ok = globalShortcut.register(accel, () => { void toggleVoice(); });
  } catch (e) {
    shortcutError = `Invalid shortcut "${accel}": ${(e as Error).message}`;
    return { ok: false, error: shortcutError };
  }
  if (!ok) {
    shortcutError = `"${accel}" is already used by another application or Windows. Choose a different shortcut in Settings → General.`;
    return { ok: false, error: shortcutError };
  }
  registeredShortcut = accel;
  shortcutError = null;
  return { ok: true };
}

function buildTray() {
  if (!tray) {
    tray = new Tray(icon('tray.png'));
    tray.setToolTip('Jarvis');
    tray.on('click', () => showMain());
  }
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open Jarvis', click: () => showMain() },
    { label: `Activate voice${registeredShortcut ? `  (${registeredShortcut.replace('Control', 'Ctrl')})` : ''}`, click: () => void toggleVoice() },
    {
      label: 'Pause listening', type: 'checkbox', checked: wakePaused,
      click: async (item) => {
        wakePaused = item.checked;
        try { await backendFetch('/api/voice/pause', { method: 'POST', body: JSON.stringify({ paused: wakePaused }) }); } catch { /* backend down */ }
      },
    },
    { label: 'Show floating assistant', click: () => showFloat() },
    { label: 'Settings', click: () => showMain('/settings') },
    { type: 'separator' },
    { label: 'Exit Jarvis', click: () => shutdown(false) },
    { label: `Emergency stop  (${EMERGENCY_SHORTCUT.replace('Control', 'Ctrl')})`, click: () => shutdown(true) },
  ]));
}

function applyDesktopSettings(next: Partial<DesktopSettings>): { ok: boolean; error?: string } {
  const prev = desktop;
  desktop = { ...desktop, ...next };
  let result: { ok: boolean; error?: string } = { ok: true };
  if (next.shortcut && (next.shortcut !== prev.shortcut || !registeredShortcut)) result = registerShortcut(next.shortcut);
  if (floatWindow) floatWindow.setAlwaysOnTop(desktop.float_always_on_top, 'floating');
  if (next.launch_at_login !== undefined && !DEV_URL) {
    // Installed app: Jarvis.exe --hidden. Launcher mode (signed electron.exe
    // running the project folder): electron.exe "<project>" --hidden.
    app.setLoginItemSettings(app.isPackaged
      ? { openAtLogin: desktop.launch_at_login, args: ['--hidden'] }
      : { openAtLogin: desktop.launch_at_login, path: process.execPath, args: [app.getAppPath(), '--hidden'] });
  }
  buildTray();
  broadcast('shortcut:status', { shortcut: registeredShortcut, error: shortcutError });
  return result;
}

async function loadDesktopSettings() {
  try {
    const res = await backendFetch('/api/settings');
    const s = (await res.json()) as { general: DesktopSettings };
    applyDesktopSettings(s.general);
  } catch {
    applyDesktopSettings({ shortcut: desktop.shortcut });
  }
}

// ------------------------------------------------------------------ IPC
function trusted(e: IpcMainInvokeEvent): boolean {
  const url = e.senderFrame?.url ?? '';
  return url.startsWith(APP_ORIGIN + '/') || (!!DEV_URL && url.startsWith(DEV_URL));
}

function handle<T>(channel: string, fn: (e: IpcMainInvokeEvent, ...args: unknown[]) => T) {
  ipcMain.handle(channel, (e, ...args) => {
    if (!trusted(e)) throw new Error('untrusted sender');
    return fn(e, ...args);
  });
}

const ACCEL = /^((Control|Ctrl|Alt|Shift|Super|CommandOrControl)\+){1,3}([A-Z0-9]|F[1-9]|F1[0-2]|Space|Enter|Tab|Backquote|Insert|Home|End|PageUp|PageDown)$/;

function registerIpc() {
  handle('backend:info', () => backend.info());
  handle('backend:restart', async () => { await backend.restart(); return backend.info(); });
  handle('app:shutdown', (_e, emergency) => { shutdown(emergency === true); });
  handle('app:version', () => ({ version: app.getVersion(), electron: process.versions.electron, packaged: app.isPackaged }));
  handle('app:metrics', () => app.getAppMetrics().map((m) => ({
    pid: m.pid, type: m.type, cpu: m.cpu.percentCPUUsage, memoryMB: Math.round(m.memory.workingSetSize / 1024),
  })));
  handle('window:show-main', (_e, route) => showMain(typeof route === 'string' && route.startsWith('/') ? route : undefined));
  handle('window:show-float', () => showFloat());
  handle('window:hide-float', () => floatWindow?.hide());
  handle('window:minimize', () => mainWindow?.minimize());
  handle('desktop:apply', (_e, raw) => {
    if (typeof raw !== 'object' || raw === null) throw new Error('invalid settings');
    const r = raw as Record<string, unknown>;
    const next: Partial<DesktopSettings> = {};
    if (typeof r.shortcut === 'string') {
      if (!ACCEL.test(r.shortcut)) return { ok: false, error: 'Unsupported shortcut format. Example: Control+Space or Alt+J.' };
      next.shortcut = r.shortcut;
    }
    for (const k of ['close_to_tray', 'minimize_to_tray', 'float_always_on_top', 'show_float_on_voice', 'launch_at_login'] as const) {
      if (typeof r[k] === 'boolean') next[k] = r[k] as boolean;
    }
    return applyDesktopSettings(next);
  });
  handle('shortcut:status', () => ({ shortcut: registeredShortcut, error: shortcutError }));
  handle('shell:open-external', (_e, url) => {
    if (typeof url !== 'string' || !/^(https?:\/\/|mailto:)/.test(url) || url.length > 4000) throw new Error('blocked url');
    return shell.openExternal(url);
  });
  handle('shell:open-folder', (_e, which) => {
    const target = which === 'logs' ? path.join(app.getPath('userData'), 'logs')
      : which === 'data' ? app.getPath('userData')
        : which === 'screenshots' ? path.join(app.getPath('userData'), 'screenshots') : null;
    if (!target) throw new Error('unknown folder');
    fs.mkdirSync(target, { recursive: true });
    return shell.openPath(target);
  });
}

// ------------------------------------------------------------- lifecycle
const CSP = [
  "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
  'connect-src http://127.0.0.1:* ws://127.0.0.1:*', "media-src 'self' http://127.0.0.1:* blob:",
  "font-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
].join('; ');

function serveRenderer() {
  protocol.handle('app', async (req) => {
    const { pathname } = new URL(req.url);
    const rel = decodeURIComponent(pathname).replace(/^\/+/, '') || 'index.html';
    const file = path.normalize(path.join(RENDERER_DIR, rel));
    if (!file.startsWith(RENDERER_DIR + path.sep)) return new Response('forbidden', { status: 403 });
    const target = fs.existsSync(file) ? file : path.join(RENDERER_DIR, 'index.html');
    const res = await enet.fetch(pathToFileURL(target).toString());
    const headers = new Headers(res.headers);
    if (target.endsWith('.html')) headers.set('Content-Security-Policy', CSP);
    return new Response(res.body, { status: res.status, headers });
  });
}

// `Jarvis.exe --quit` exits a running (tray-resident) instance cleanly.
// Launching Jarvis again while it runs (e.g. clicking the shortcut) asks what to do.
// `Jarvis.exe --quit` shuts down without asking.
app.on('second-instance', async (_e, argv) => {
  if (argv.includes('--quit')) { shutdown(false); return; }
  if (askingOnRelaunch) return;
  askingOnRelaunch = true;
  try {
    const { response } = await dialog.showMessageBox({
      type: 'question', title: 'Jarvis is already running', icon: icon('icon.png'),
      message: 'Jarvis is already running.',
      detail: 'Shutting down stops listening (the microphone is released), cancels running tasks and closes the backend.',
      buttons: ['Open Jarvis', 'Shut down Jarvis', 'Cancel'], defaultId: 0, cancelId: 2, noLink: true,
    });
    if (response === 0) showMain();
    else if (response === 1) shutdown(false);
  } finally {
    askingOnRelaunch = false;
  }
});

/**
 * Stop Jarvis completely.
 * - graceful: cancel tasks, release the mic, stop the backend (API shutdown, then kill), exit.
 * - emergency: kill the backend process tree immediately (including commands it started) and exit.
 */
function shutdown(emergency: boolean) {
  if (shuttingDown) return;
  shuttingDown = true;
  quitting = true;
  globalShortcut.unregisterAll();
  if (!emergency) { app.quit(); return; }
  void backend.kill().finally(() => {
    tray?.destroy();
    app.exit(0);
  });
}

app.whenReady().then(async () => {
  // The renderer never needs device permissions: the mic is captured by the backend.
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  serveRenderer();
  registerIpc();
  stampLauncherShortcuts();
  // Emergency stop works even while Jarvis is hidden in the tray.
  try { globalShortcut.register(EMERGENCY_SHORTCUT, () => shutdown(true)); } catch { /* taken by another app */ }
  backend.on('status', (info) => {
    broadcast('backend:status', info);
    if (info.status === 'ready') void loadDesktopSettings();
  });
  createMainWindow();
  buildTray();
  await backend.start();
});

app.on('window-all-closed', () => {
  // Stay resident in the tray.
});

app.on('before-quit', (e) => {
  if (backend.status === 'stopped') return;
  quitting = true;
  e.preventDefault();
  globalShortcut.unregisterAll();
  void backend.stop().finally(() => {
    tray?.destroy();
    app.exit(0);
  });
});
