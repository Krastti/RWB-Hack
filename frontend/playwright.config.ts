import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:4200', headless: true },
  webServer: { command: 'npm start', url: 'http://127.0.0.1:4200', reuseExistingServer: !process.env['CI'], timeout: 60_000 },
});
