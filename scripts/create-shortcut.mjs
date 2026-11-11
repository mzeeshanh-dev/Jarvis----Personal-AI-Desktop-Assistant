// `npm run shortcut`: creates Desktop and Start-menu shortcuts that start Jarvis
// through publisher-signed runtimes (Electron's electron.exe + the venv's
// python.exe) instead of the unsigned packaged Jarvis.exe. This works with
// Windows Smart App Control left ON. The project folder must stay in place.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { ROOT, run, venvPython } from './lib.mjs';

const electron = path.join(ROOT, 'node_modules', 'electron', 'dist', 'electron.exe');
if (!fs.existsSync(electron)) throw new Error('Electron is not installed. Run `npm install` first.');
if (!venvPython()) throw new Error('backend/.venv is missing. Run `npm run setup` first.');

// The launcher runs the production renderer build (no dev server).
console.log('[shortcut] building frontend');
await run(process.execPath, [path.join(ROOT, 'scripts', 'build-frontend.mjs')]);

const icon = path.join(ROOT, 'resources', 'icons', 'icon.ico');
const targets = [
  path.join(os.homedir(), 'Desktop', 'Jarvis.lnk'),
  path.join(process.env.APPDATA ?? '', 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Jarvis.lnk'),
];
const q = (s) => s.replace(/'/g, "''");
for (const lnk of targets) {
  const ps = [
    `$s = (New-Object -ComObject WScript.Shell).CreateShortcut('${q(lnk)}')`,
    `$s.TargetPath = '${q(electron)}'`,
    `$s.Arguments = '"${q(ROOT)}"'`,
    `$s.WorkingDirectory = '${q(ROOT)}'`,
    `$s.IconLocation = '${q(icon)},0'`,
    `$s.Description = 'Jarvis desktop assistant'`,
    '$s.Save()',
  ].join('; ');
  execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', ps], { stdio: 'inherit' });
  console.log(`[shortcut] created ${lnk}`);
}
// Explorer caches shortcut icons; refresh so a regenerated icon.ico shows up immediately.
try {
  execFileSync(path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'ie4uinit.exe'), ['-show'], { stdio: 'ignore' });
} catch { /* cache refresh is best effort */ }
console.log('[shortcut] done. Start Jarvis from the Desktop or Start menu; quit from the tray icon or the sidebar Shut down button.');
