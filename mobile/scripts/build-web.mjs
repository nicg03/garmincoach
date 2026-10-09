import { cp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const mobile = resolve(here, '..');
const root = resolve(mobile, '..');
const source = resolve(root, 'server/static');
const output = resolve(mobile, 'www');

await rm(output, { recursive: true, force: true });
await mkdir(resolve(output, 'static'), { recursive: true });
await cp(resolve(source, 'css'), resolve(output, 'static/css'), { recursive: true });
await cp(resolve(source, 'js'), resolve(output, 'static/js'), { recursive: true });

let html = await readFile(resolve(source, 'index.html'), 'utf8');
html = html.replace(
  '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>',
  '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>\\n' +
  '<script src="/native.js"></script>',
);
await writeFile(resolve(output, 'index.html'), html);

await build({
  entryPoints: [resolve(mobile, 'src/native.ts')],
  outfile: resolve(output, 'native.js'),
  bundle: true,
  minify: true,
  format: 'iife',
  platform: 'browser',
  target: ['safari15', 'chrome100'],
});

await writeFile(resolve(output, 'offline.html'), `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="theme-color" content="#111111"><title>gepard.fit</title>
<style>body{font:16px system-ui;margin:0;background:#f5f5f3;color:#111;display:grid;min-height:100vh;place-items:center}
main{max-width:340px;padding:32px;text-align:center}button{padding:12px 18px;border:0;border-radius:8px;background:#2154ff;color:#fff;font-weight:600}</style>
</head><body><main><h1>gepard.fit</h1><p>No network connection. Your data is safe; reconnect and try again.</p>
<button onclick="location.href='/'">Try again</button></main></body></html>`);

console.log(`Built mobile web assets in ${output}`);
