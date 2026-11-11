// Runs the backend virtualenv's Python with the given arguments.
import { run, venvPython, BACKEND } from './lib.mjs';

const py = venvPython();
if (!py) {
  console.error('backend/.venv not found. Run `npm run setup` first.');
  process.exit(1);
}
try {
  await run(py, process.argv.slice(2), { env: { ...process.env, PYTHONPATH: BACKEND } });
} catch (e) {
  console.error(e.message);
  process.exit(1);
}
