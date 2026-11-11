// Packages the Python backend with PyInstaller into build/backend/jarvis-backend/.
import fs from 'node:fs';
import path from 'node:path';
import { BACKEND, ROOT, run, venvPython } from './lib.mjs';

const py = venvPython();
if (!py) {
  console.error('backend/.venv not found. Run `npm run setup` first.');
  process.exit(1);
}
const out = path.join(ROOT, 'build', 'backend');
const work = path.join(ROOT, 'build', 'pyinstaller-work');
fs.rmSync(out, { recursive: true, force: true });
await run(py, ['-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
  '--distpath', out, '--workpath', work, path.join(BACKEND, 'jarvis-backend.spec')], { cwd: BACKEND });
const exe = path.join(out, 'jarvis-backend', 'jarvis-backend.exe');
if (!fs.existsSync(exe)) throw new Error(`PyInstaller did not produce ${exe}`);
console.log(`[build] backend -> ${exe}`);
