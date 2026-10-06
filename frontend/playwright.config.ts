import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  retries: 0,
  // One browser at a time: every spec drives the same local backend, and the
  // outfit page deliberately spaces its image lookups out, so parallel workers
  // otherwise fail each other on timeouts rather than on assertions.
  workers: 1,
  outputDir: '/tmp/itp-playwright-results',
  use: {
    baseURL: process.env.ITP_E2E_BASE_URL || 'http://127.0.0.1:8000',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { ...devices['Pixel 7'], viewport: { width: 412, height: 915 } } },
  ],
});
