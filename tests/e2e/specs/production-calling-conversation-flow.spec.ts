import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { auditDir, requiredEnv } from '../src/env';


async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_EMAIL'));
  await page.getByLabel('Password', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_PASSWORD'));
  await Promise.all([
    page.waitForURL(/\/dashboard/, { waitUntil: 'domcontentloaded' }),
    page.getByRole('button', { name: 'Login', exact: true }).click(),
  ]);
}


test('Allstate Conversation Flow successor is healthy without placing a call', async ({ page }) => {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText || '';
    const url = new URL(request.url());
    const benignNextNavigationAbort = (
      request.method() === 'GET'
      && failure.includes('ERR_ABORTED')
      && (url.searchParams.has('_rsc') || request.resourceType() === 'document')
    );
    if (!benignNextNavigationAbort) {
      failedRequests.push(`${request.method()} ${request.url()} ${failure}`.trim());
    }
  });
  page.on('response', (response) => {
    if (response.status() >= 400 && !response.url().endsWith('/favicon.ico')) {
      failedRequests.push(`${response.status()} ${response.url()}`);
    }
  });

  await login(page);
  await page.goto('/calling', { waitUntil: 'networkidle' });
  await expect(page.getByRole('heading', { name: 'Calling Channel Workspace' })).toBeVisible();
  await expect(page.getByText('Conversation Flow active').locator('..')).toContainText('Ready');
  await expect(page.getByText('Legacy agent preserved').locator('..')).toContainText('Ready');
  await expect(page.getByText('Outbound number assigned').locator('..')).toContainText('Ready');
  await expect(page.getByText('Provider health').locator('..')).toContainText('Internal test ready');
  await expect(page.getByText('Prospect calling disabled').locator('..')).toContainText('Ready');
  await expect(page.getByText('Batch queue disabled').locator('..')).toContainText('Ready');
  await expect(page.getByText('Current blockers')).toHaveCount(0);
  await expect(page.getByText('Assignment warnings')).toHaveCount(0);
  await expect(page.getByLabel('Confirmation')).toHaveAttribute(
    'placeholder',
    'PLACE CONVERSATION-FLOW INTERNAL TEST CALL',
  );
  await expect(page.getByRole('button', { name: 'Place Conversation-Flow Internal Test Call' })).toBeDisabled();
  await expect.poll(() => page.locator('table.ops-table tbody tr').count()).toBeGreaterThanOrEqual(7);
  await page.screenshot({
    path: path.join(auditDir(), 'screenshots', 'calling-conversation-flow-successor.png'),
    fullPage: true,
  });

  expect(consoleErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
});
