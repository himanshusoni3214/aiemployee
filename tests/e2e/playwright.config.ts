import { defineConfig } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const auditDir = path.resolve(process.env.QA_AUDIT_DIR || path.join(process.cwd(), 'audits', `local-${Date.now()}`));
fs.mkdirSync(auditDir, { recursive: true });
fs.mkdirSync(path.join(auditDir, 'screenshots'), { recursive: true });
fs.mkdirSync(path.join(auditDir, 'route-results'), { recursive: true });

export default defineConfig({
  testDir: './specs',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  outputDir: path.join(auditDir, 'playwright-output'),
  reporter: [
    ['list'],
    ['json', { outputFile: path.join(auditDir, 'playwright-results.json') }],
    ['html', { outputFolder: path.join(auditDir, 'playwright-report'), open: 'never' }],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'https://ops.themealz.com',
    trace: 'on',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
});
