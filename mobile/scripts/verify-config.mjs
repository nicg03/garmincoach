import { access, readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobile = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const config = await readFile(resolve(mobile, 'capacitor.config.ts'), 'utf8');
const pkg = JSON.parse(await readFile(resolve(mobile, 'package.json'), 'utf8'));

const problems = [];
if (!config.includes("appId: 'fit.gepard.app'")) problems.push('unexpected appId');
if (!config.includes("webDir: 'www'")) problems.push('webDir must be www');
if (config.includes('server.url')) problems.push('production builds must bundle web assets');
if (!/^\d+\.\d+\.\d+$/.test(pkg.version)) problems.push('package version must be semver');

for (const path of ['www/index.html', 'ios/App/App/Info.plist', 'android/app/build.gradle']) {
  try {
    await access(resolve(mobile, path));
  } catch {
    problems.push(`missing ${path}; run npm run sync`);
  }
}

if (problems.length) {
  console.error(problems.map((problem) => `- ${problem}`).join('\n'));
  process.exitCode = 1;
} else {
  console.log(`gepard.fit mobile ${pkg.version}: configuration OK`);
}
