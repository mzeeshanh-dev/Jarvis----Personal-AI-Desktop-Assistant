// Creates backend/.venv, installs Python deps, and downloads voice models.
import { run, VENV, BACKEND, venvPython, systemPython } from './lib.mjs';
import path from 'node:path';

if (!venvPython()) {
  console.log('[setup] creating Python virtual environment');
  const py = systemPython();
  await run(py, py === 'py' ? ['-3.11', '-m', 'venv', VENV] : ['-m', 'venv', VENV]);
}
const py = venvPython();
await run(py, ['-m', 'pip', 'install', '--upgrade', 'pip']);
await run(py, ['-m', 'pip', 'install', '-r', path.join(BACKEND, 'requirements-dev.txt')]);
await run(py, [path.join(BACKEND, 'scripts', 'fetch_models.py')]);
console.log('[setup] done');
