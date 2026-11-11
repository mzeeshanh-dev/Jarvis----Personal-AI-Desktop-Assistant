// esbuild configuration for the Electron main and preload bundles.
import { build, context } from 'esbuild';
import path from 'node:path';
import { ROOT } from './lib.mjs';

const common = {
  bundle: true,
  platform: 'node',
  format: 'cjs',
  target: 'node22',
  external: ['electron'],
  sourcemap: true,
  logLevel: 'warning',
};

const entries = [
  { entryPoints: [path.join(ROOT, 'frontend/src/main/main.ts')], outfile: path.join(ROOT, 'frontend/dist-electron/main.js') },
  { entryPoints: [path.join(ROOT, 'frontend/src/preload/preload.ts')], outfile: path.join(ROOT, 'frontend/dist-electron/preload.js') },
];

export async function buildElectron({ production = false } = {}) {
  await Promise.all(entries.map((e) => build({ ...common, ...e, minify: production, sourcemap: !production })));
}

export async function watchElectron(onRebuild) {
  const ctxs = await Promise.all(entries.map((e) => context({
    ...common, ...e,
    plugins: [{ name: 'notify', setup(b) { b.onEnd((r) => { if (!r.errors.length) onRebuild(); }); } }],
  })));
  await Promise.all(ctxs.map((c) => c.watch()));
  return ctxs;
}
