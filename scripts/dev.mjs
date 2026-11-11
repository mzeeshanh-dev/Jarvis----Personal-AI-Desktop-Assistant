// `npm run dev`: Vite dev server + esbuild watch for main/preload + Electron.
// Electron itself starts and supervises the Python backend.
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { createServer } from 'vite';
import { ROOT, venvPython } from './lib.mjs';
import { watchElectron } from './electron-build.mjs';

if (!venvPython()) {
  console.error('[dev] backend/.venv not found. Run `npm run setup` once first.');
  process.exit(1);
}

const require = createRequire(import.meta.url);
const electronBin = require('electron');

const server = await createServer({ configFile: path.join(ROOT, 'frontend/vite.config.mts') });
await server.listen();
const url = server.resolvedUrls.local[0];
console.log(`[dev] renderer at ${url}`);

let child = null;
let restarting = false;
let debounce = null;

function launch() {
  const env = { ...process.env, JARVIS_DEV_SERVER_URL: url.replace(/\/$/, '') };
  // VS Code's integrated terminal exports this, which makes Electron run as plain Node.
  delete env.ELECTRON_RUN_AS_NODE;
  child = spawn(electronBin, [ROOT], { stdio: 'inherit', env });
  child.on('exit', (code) => {
    if (restarting) return;
    console.log(`[dev] electron exited (${code}); stopping dev server`);
    void server.close().then(() => process.exit(code ?? 0));
  });
}

function relaunch() {
  clearTimeout(debounce);
  debounce = setTimeout(() => {
    if (!child) return launch();
    restarting = true;
    child.once('exit', () => { restarting = false; launch(); });
    child.kill();
  }, 300);
}

let first = true;
await watchElectron(() => {
  if (first) { first = false; launch(); return; }
  console.log('[dev] main/preload changed; restarting electron');
  relaunch();
});

for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { child?.kill(); void server.close().then(() => process.exit(0)); });
}
