// Production build of the renderer (Vite) and Electron main/preload (esbuild).
import path from 'node:path';
import { build } from 'vite';
import { ROOT } from './lib.mjs';
import { buildElectron } from './electron-build.mjs';

await build({ configFile: path.join(ROOT, 'frontend/vite.config.mts'), mode: 'production' });
await buildElectron({ production: true });
console.log('[build] frontend done');
