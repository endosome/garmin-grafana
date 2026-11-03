import { defineConfig } from '@playwright/test';

const baseURL = (process.env.GRAFANA_URL ?? 'http://localhost:3000').replace(/\/$/, '');

const basic = Buffer.from(`${process.env.GRAFANA_USER ?? 'admin'}:${process.env.GRAFANA_PASSWORD ?? ''}`).toString('base64');

export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  expect: { timeout: 15_000, toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled' } },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}{ext}',
  use: {
    baseURL,
    extraHTTPHeaders: process.env.GRAFANA_PASSWORD ? { Authorization: `Basic ${basic}` } : {},
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    locale: 'en-US',
    colorScheme: 'dark',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
