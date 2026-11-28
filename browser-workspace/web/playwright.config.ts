import { existsSync } from 'node:fs';
import { defineConfig } from '@playwright/test';
const live = process.env.DOXA_LIVE_URL;
export default defineConfig({
  testDir: './tests',
  testMatch: 'documents.spec.ts',
  workers: 1,
  timeout: 90_000,
  expect: {timeout: 15_000},
  use: {
    baseURL: live || 'http://127.0.0.1:8141',
    browserName: 'chromium',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined) },
  },
  webServer: live ? undefined : {
    command: 'python3 -m http.server 8141 --bind 127.0.0.1 --directory ../release_staging/space',
    url: 'http://127.0.0.1:8141/dist/',
    reuseExistingServer: false,
    stdout: 'ignore',
    stderr: 'ignore',
  },
});
