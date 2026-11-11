// End-to-end: the real Electron app + real Python backend (no Groq key needed).
// The app is started the way a user starts it and driven over the Chrome
// DevTools Protocol (Playwright's _electron launcher does not attach to this
// app's windows with Electron 45).
import { chromium, expect, test, type Browser, type Page } from '@playwright/test';
import { execSync, spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';

const ROOT = path.resolve(__dirname, '..', '..');
// JARVIS_E2E_EXE=dist-installer\win-unpacked\Jarvis.exe runs the suite against the packaged app.
const PACKAGED = process.env.JARVIS_E2E_EXE;
const ELECTRON = PACKAGED ?? path.join(ROOT, 'node_modules', 'electron', 'dist', 'electron.exe');
const APP_ARGS = PACKAGED ? [] : [ROOT];
const DATA = path.join(ROOT, 'tests', '.work', `e2e-${Date.now()}`);

function env() {
  const e: Record<string, string> = { ...(process.env as Record<string, string>), JARVIS_DEV_SERVER_URL: '' };
  delete e.ELECTRON_RUN_AS_NODE; // set by VS Code terminals; would start Electron as plain Node
  // Playwright workers inject their TS loader via NODE_OPTIONS; Electron's main would load it too.
  for (const k of Object.keys(e)) if (k === 'NODE_OPTIONS' || k.startsWith('PW_') || k.startsWith('PLAYWRIGHT')) delete e[k];
  return e;
}

function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const s = net.createServer().listen(0, '127.0.0.1', () => {
      const p = (s.address() as net.AddressInfo).port;
      s.close(() => resolve(p));
    });
  });
}

function childrenOf(pid: number): { pid: number; name: string }[] {
  const out = execSync(`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter 'ParentProcessId=${pid}' | ForEach-Object { \\"$($_.ProcessId) $($_.Name)\\" }"`).toString();
  return out.split(/\r?\n/).filter(Boolean).map((l) => { const [p, ...n] = l.trim().split(' '); return { pid: Number(p), name: n.join(' ') }; });
}

const alive = (pid: number) => { try { process.kill(pid, 0); return true; } catch { return false; } };

async function backendPid(mainPid: number): Promise<number> {
  for (let i = 0; i < 100; i++) {
    const py = childrenOf(mainPid).find((c) => /python|jarvis-backend/i.test(c.name));
    if (py) return py.pid;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error('backend process not found');
}

let proc: ChildProcess;
let browser: Browser;
let page: Page;

test.beforeAll(async () => {
  fs.mkdirSync(DATA, { recursive: true });
  const port = await freePort();
  const started = Date.now();
  proc = spawn(ELECTRON, [...APP_ARGS, `--user-data-dir=${DATA}`, `--remote-debugging-port=${port}`], { env: env(), stdio: ['ignore', 'pipe', 'pipe'] });
  const elog = fs.createWriteStream(path.join(DATA, 'electron-stdio.log'));
  proc.stdout?.pipe(elog);
  proc.stderr?.pipe(elog);
  // Wait until the main window's page exists, then attach.
  await expect.poll(async () => {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json() as { url: string }[];
      return targets.some((t) => t.url.startsWith('app://jarvis/index.html'));
    } catch { return false; }
  }, { timeout: 60_000, intervals: [500] }).toBe(true);
  browser = await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
  page = browser.contexts().flatMap((c) => c.pages()).find((p) => p.url().startsWith('app://jarvis/index.html'))!;
  expect(page).toBeTruthy();
  await expect(page.getByText('How can I help?')).toBeVisible({ timeout: 60_000 });
  await expect(page.locator('span[title="Connected"]')).toBeVisible({ timeout: 60_000 });
  console.log(`cold start to connected UI: ${Date.now() - started} ms`);
});

test.afterAll(async () => {
  await browser?.close().catch(() => {});
  if (proc?.pid && alive(proc.pid)) execSync(`taskkill /PID ${proc.pid} /T /F`);
  fs.rmSync(DATA, { recursive: true, force: true, maxRetries: 5, retryDelay: 500 });
});

test('renderer is sandboxed and branded', async () => {
  expect(await page.title()).toBe('Jarvis');
  expect(await page.evaluate(() => typeof (window as any).require)).toBe('undefined');
  expect(await page.evaluate(() => typeof (window as any).process)).toBe('undefined');
  const bridgeKeys = await page.evaluate(() => Object.keys((window as any).jarvis).sort());
  expect(bridgeKeys).toContain('backendInfo');
  expect(bridgeKeys).not.toContain('ipcRenderer');
  // The restricted bridge refuses non-https/mailto URLs.
  const blocked = await page.evaluate(() => (window as any).jarvis.openExternal('file:///C:/Windows/win.ini').then(() => 'opened', (e: Error) => e.message));
  expect(blocked).toContain('blocked url');
});

test('backend rejects requests without the launch token', async () => {
  const info = await page.evaluate(() => (window as any).jarvis.backendInfo());
  expect(info.status).toBe('ready');
  expect((await fetch(`${info.url}/api/settings`)).status).toBe(401);
  expect((await fetch(`${info.url}/api/settings`, { headers: { 'X-Jarvis-Token': info.token } })).status).toBe(200);
});

test('launch token never reaches the backend log', async () => {
  const info = await page.evaluate(() => (window as any).jarvis.backendInfo());
  const log = fs.readFileSync(path.join(DATA, 'logs', 'backend.log'), 'utf8');
  expect(log).toContain('WebSocket /ws?token=[REDACTED]');
  expect(log).not.toContain(info.token);
});

test('text command without a Groq key explains what to configure', async () => {
  await page.getByPlaceholder('Ask Jarvis to do something…').fill('summarize my week and tell me what is pending');
  await page.keyboard.press('Enter');
  await expect(page.getByText(/No Groq API key is configured/)).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('aside').getByText('summarize my week', { exact: false })).toBeVisible();
});

test('settings sections render', async () => {
  await page.locator('nav').getByRole('button', { name: 'Settings' }).click();
  for (const s of ['General', 'Voice', 'AI providers', 'Groq API keys', 'Permissions', 'Browser', 'Desktop automation', 'Gmail', 'Performance', 'Logs & diagnostics', 'About']) {
    await page.getByRole('button', { name: s, exact: true }).click();
    await expect(page.locator('h2', { hasText: s })).toBeVisible();
  }
  await page.getByRole('button', { name: 'Permissions', exact: true }).click();
  await expect(page.getByText('delete_file')).toBeVisible();
  await page.getByRole('button', { name: 'Performance', exact: true }).click();
  await expect(page.getByText('Backend RAM')).toBeVisible();
  await page.locator('nav').getByRole('button', { name: 'Assistant' }).click();
});

test('global push-to-talk shortcut is registered or a conflict is reported', async () => {
  const status = await page.evaluate(() => (window as any).jarvis.shortcutStatus());
  expect(status.shortcut === 'Control+Space' || !!status.error).toBe(true);
  console.log(`shortcut status: ${JSON.stringify(status)}`);
});

test('voice activation from the UI reaches the backend voice service', async () => {
  const info = await page.evaluate(() => (window as any).jarvis.backendInfo());
  const before = await (await fetch(`${info.url}/api/voice/status`, { headers: { 'X-Jarvis-Token': info.token } })).json();
  expect(before.state).toBe('idle');
  await page.getByRole('button', { name: 'Speak (push-to-talk)' }).click();
  // The mic opens (listening) or reports a microphone error; either proves activation is wired end to end.
  await expect(page.getByText(/Listening|Microphone problem/).first()).toBeVisible({ timeout: 10_000 });
  await page.getByRole('button', { name: 'Stop everything' }).click().catch(() => {});
  await expect.poll(async () => (await (await fetch(`${info.url}/api/voice/status`, { headers: { 'X-Jarvis-Token': info.token } })).json()).state, { timeout: 15_000 })
    .toMatch(/idle|error/);
});

test('backend crash is detected and restarted', async () => {
  const before = await backendPid(proc.pid!);
  execSync(`taskkill /PID ${before} /T /F`);
  await expect.poll(async () => (await page.evaluate(() => (window as any).jarvis.backendInfo())).status, { timeout: 60_000 }).toBe('ready');
  await expect(page.locator('span[title="Connected"]')).toBeVisible({ timeout: 30_000 });
  const after = await backendPid(proc.pid!);
  expect(after).not.toBe(before);
  expect((await page.evaluate(() => (window as any).jarvis.backendInfo())).restarts).toBeGreaterThanOrEqual(1);
});

test('Jarvis --quit stops the app and its backend (no orphan processes)', async () => {
  const pid = await backendPid(proc.pid!);
  await browser.close();
  spawn(ELECTRON, [...APP_ARGS, `--user-data-dir=${DATA}`, '--quit'], { env: env(), stdio: 'ignore' });
  await expect.poll(() => alive(pid), { timeout: 20_000 }).toBe(false);
  await expect.poll(() => alive(proc.pid!), { timeout: 20_000 }).toBe(false);
});
