import { defineConfig } from '@playwright/test';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

// Root package.json has NO "type": "module" (the Vercel `api/*.js`
// serverless functions are plain CommonJS) — Playwright transpiles this
// .ts config to CJS accordingly, so `__dirname` is the ambient CJS global,
// never `import.meta.url` (ESM-only, would crash that transpile).
declare const __dirname: string;

// D3 (planeacion-flujo-confiable): load E2E_ADMIN_EMAIL/E2E_ADMIN_PASSWORD
// (and anything else already needed by the integracion_F1 tooling) from
// integracion_F1/.env as a FALLBACK only — real shell/CI env vars always
// win. Minimal inline parser (KEY=VALUE lines, '#' comments, optional
// quotes) rather than a new devDependency; values are NEVER logged/printed.
function loadDotEnvFallback(envPath: string): void {
  if (!existsSync(envPath)) return;
  const text = readFileSync(envPath, 'utf-8');
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (key && process.env[key] === undefined) process.env[key] = value;
  }
}

loadDotEnvFallback(path.join(__dirname, 'integracion_F1', '.env'));

export default defineConfig({
  testDir: './e2e',
  testMatch: '**/*.spec.ts',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'https://sismo-cali-dashboard.vercel.app',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
});
