// Manages the Python backend as a supervised child process:
// dynamic port + per-launch auth token, health checks, crash restarts with
// backoff, and clean shutdown (graceful API call, then process-tree kill).
import { app } from 'electron';
import { ChildProcess, spawn, execFile } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';

export type BackendStatus = 'stopped' | 'starting' | 'ready' | 'restarting' | 'failed';

const EXIT_PORT_IN_USE = 98;
const HEALTH_TIMEOUT_MS = 45_000;
const HEARTBEAT_MS = 20_000;
const MAX_RESTARTS = 5;
const RESTART_WINDOW_MS = 5 * 60_000;

export interface BackendInfo {
  status: BackendStatus;
  url: string | null;
  wsUrl: string | null;
  token: string | null;
  error: string | null;
  restarts: number;
}

export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

interface Command { cmd: string; args: string[]; cwd: string }

export class BackendManager extends EventEmitter {
  status: BackendStatus = 'stopped';
  port = 0;
  token = '';
  error: string | null = null;
  private child: ChildProcess | null = null;
  private stopping = false;
  private restartTimes: number[] = [];
  private portRetries: number[] = [];
  private heartbeat: NodeJS.Timeout | null = null;
  private heartbeatFailures = 0;
  private logStream: fs.WriteStream | null = null;

  constructor(private readonly opts: { allowedOrigins: string[]; root: string }) {
    super();
  }

  info(): BackendInfo {
    const ready = this.status === 'ready';
    return {
      status: this.status,
      url: ready ? `http://127.0.0.1:${this.port}` : null,
      wsUrl: ready ? `ws://127.0.0.1:${this.port}/ws` : null,
      token: ready ? this.token : null,
      error: this.error,
      restarts: this.restartTimes.length,
    };
  }

  private setStatus(s: BackendStatus, error: string | null = null) {
    this.status = s;
    this.error = error;
    this.emit('status', this.info());
  }

  private command(): Command {
    if (app.isPackaged) {
      const dir = path.join(process.resourcesPath, 'backend');
      return { cmd: path.join(dir, 'jarvis-backend.exe'), args: [], cwd: dir };
    }
    const backend = path.join(this.opts.root, 'backend');
    const py = path.join(backend, '.venv', 'Scripts', 'python.exe');
    if (!fs.existsSync(py)) throw new Error('backend/.venv is missing. Run `npm run setup` first.');
    return { cmd: py, args: [path.join(backend, 'main.py')], cwd: backend };
  }

  private openLog() {
    const dir = path.join(app.getPath('userData'), 'logs');
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, 'backend-console.log');
    try {
      if (fs.existsSync(file) && fs.statSync(file).size > 5_000_000) fs.renameSync(file, file + '.1');
    } catch { /* rotation is best effort */ }
    this.logStream = fs.createWriteStream(file, { flags: 'a' });
  }

  async start(): Promise<void> {
    this.stopping = false;
    if (!this.logStream) this.openLog();
    this.setStatus(this.restartTimes.length ? 'restarting' : 'starting');
    let command: Command;
    try {
      command = this.command();
    } catch (e) {
      this.setStatus('failed', (e as Error).message);
      return;
    }
    this.port = await freePort();
    this.token = randomBytes(32).toString('hex');
    const resources = app.isPackaged ? process.resourcesPath : path.join(this.opts.root, 'resources');
    const child = spawn(command.cmd, [...command.args, '--port', String(this.port)], {
      cwd: command.cwd,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        JARVIS_AUTH_TOKEN: this.token,
        JARVIS_DATA_DIR: app.getPath('userData'),
        JARVIS_RESOURCES_DIR: resources,
        JARVIS_ALLOWED_ORIGINS: this.opts.allowedOrigins.join(','),
        JARVIS_PARENT_PID: String(process.pid),
        JARVIS_APP_VERSION: app.getVersion(), // single source of truth: package.json
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
      },
    });
    this.child = child;
    child.stdout?.on('data', (d) => this.logStream?.write(d));
    child.stderr?.on('data', (d) => this.logStream?.write(d));
    child.on('error', (err) => this.logStream?.write(`[electron] spawn error: ${err.message}\n`));
    child.on('exit', (code, signal) => this.onExit(child, code, signal));

    const healthy = await this.waitHealthy(child);
    if (healthy) {
      this.heartbeatFailures = 0;
      this.startHeartbeat();
      this.setStatus('ready');
    } else if (this.child === child && !this.stopping && child.exitCode === null) {
      this.logStream?.write('[electron] backend failed health check; killing\n');
      await killTree(child.pid);
    }
  }

  private async waitHealthy(child: ChildProcess): Promise<boolean> {
    const deadline = Date.now() + HEALTH_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (child.exitCode !== null || this.child !== child) return false;
      if (await this.ping(1500)) return true;
      await sleep(250);
    }
    return false;
  }

  async ping(timeoutMs = 4000): Promise<boolean> {
    try {
      const res = await fetch(`http://127.0.0.1:${this.port}/health`, { signal: AbortSignal.timeout(timeoutMs) });
      if (!res.ok) return false;
      const body = (await res.json()) as { ready?: boolean };
      return body.ready === true;
    } catch {
      return false;
    }
  }

  private startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeat = setInterval(async () => {
      if (this.status !== 'ready') return;
      if (await this.ping(5000)) {
        this.heartbeatFailures = 0;
        return;
      }
      this.heartbeatFailures += 1;
      if (this.heartbeatFailures >= 3 && this.child) {
        this.logStream?.write('[electron] backend unresponsive; restarting\n');
        await killTree(this.child.pid);
      }
    }, HEARTBEAT_MS);
  }

  private stopHeartbeat() {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = null;
  }

  private onExit(child: ChildProcess, code: number | null, signal: NodeJS.Signals | null) {
    if (this.child !== child) return;
    this.child = null;
    this.stopHeartbeat();
    this.logStream?.write(`[electron] backend exited code=${code} signal=${signal}\n`);
    if (this.stopping) {
      this.setStatus('stopped');
      return;
    }
    const now = Date.now();
    this.portRetries = this.portRetries.filter((t) => now - t < 60_000);
    if (code === EXIT_PORT_IN_USE && this.portRetries.length < 10) {
      this.portRetries.push(now);
      void this.start(); // port race: pick another port immediately (bounded)
      return;
    }
    this.restartTimes = this.restartTimes.filter((t) => now - t < RESTART_WINDOW_MS);
    if (this.restartTimes.length >= MAX_RESTARTS) {
      this.setStatus('failed', `The backend crashed ${MAX_RESTARTS} times in 5 minutes. See logs/backend-console.log.`);
      return;
    }
    this.restartTimes.push(now);
    const delay = Math.min(15_000, 1000 * 2 ** (this.restartTimes.length - 1));
    this.setStatus('restarting', `Backend stopped unexpectedly (code ${code}); restarting in ${Math.round(delay / 1000)}s.`);
    setTimeout(() => { if (!this.stopping) void this.start(); }, delay);
  }

  async restart(): Promise<void> {
    this.restartTimes = [];
    await this.stop();
    await this.start();
  }

  /** Emergency stop: kill the backend and every process it started, no grace period. */
  async kill(): Promise<void> {
    this.stopping = true;
    this.stopHeartbeat();
    const child = this.child;
    this.child = null;
    if (child?.pid && child.exitCode === null) await killTree(child.pid);
    this.setStatus('stopped');
  }

  async stop(): Promise<void> {
    this.stopping = true;
    this.stopHeartbeat();
    const child = this.child;
    if (!child || child.exitCode !== null) {
      this.setStatus('stopped');
      return;
    }
    const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
    try {
      await fetch(`http://127.0.0.1:${this.port}/api/shutdown`, {
        method: 'POST', headers: { 'X-Jarvis-Token': this.token }, signal: AbortSignal.timeout(1500),
      });
    } catch { /* fall through to kill */ }
    const graceful = await Promise.race([exited.then(() => true), sleep(4000).then(() => false)]);
    if (!graceful) await killTree(child.pid);
    this.child = null;
    this.setStatus('stopped');
  }
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export function killTree(pid: number | undefined): Promise<void> {
  return new Promise((resolve) => {
    if (!pid) return resolve();
    if (process.platform === 'win32') {
      execFile('taskkill', ['/PID', String(pid), '/T', '/F'], { windowsHide: true }, () => resolve());
    } else {
      try { process.kill(pid, 'SIGKILL'); } catch { /* already gone */ }
      resolve();
    }
  });
}
