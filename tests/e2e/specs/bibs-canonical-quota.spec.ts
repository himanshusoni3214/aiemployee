import { expect, test, type Page } from '@playwright/test';
import { BREW_COMPANY_ID, requiredEnv } from '../src/env';

const LEAD_RESEARCH_CAMPAIGN_ID = 'campaign-brew-it-by-sash-lead-research';
const OUTREACH_CAMPAIGN_ID = 'campaign-brew-it-by-sash-outreach';

async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_EMAIL'));
  await page.getByLabel('Password', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_PASSWORD'));
  await Promise.all([
    page.waitForURL(/\/dashboard/, { waitUntil: 'domcontentloaded' }),
    page.getByRole('button', { name: 'Login', exact: true }).click(),
  ]);
}

async function apiGet(page: Page, path: string) {
  return page.evaluate(async (pathName) => {
    const token = window.localStorage.getItem('token');
    if (!token) throw new Error('Missing authenticated browser token');
    const response = await fetch(`/api${pathName}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });
    const body = await response.text();
    if (!response.ok) throw new Error(`GET /api${pathName} failed (${response.status}): ${body}`);
    return JSON.parse(body);
  }, path);
}

test('BIBS canonical email quota is consistent across review and outreach workflow', async ({ page }) => {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('response', (response) => {
    const status = response.status();
    if (status >= 400 && !response.url().endsWith('/favicon.ico')) {
      failedRequests.push(`${status} ${response.url()}`);
    }
  });

  await login(page);
  await page.goto(`/campaigns?company_id=${BREW_COMPANY_ID}`, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Email Marketing Workflow', exact: true })).toBeVisible();

  const review = await apiGet(page, `/campaigns/${LEAD_RESEARCH_CAMPAIGN_ID}/lead-review`);
  const readiness = await apiGet(page, `/campaigns/${OUTREACH_CAMPAIGN_ID}/outreach-send/status`);
  const preview = await apiGet(page, `/campaigns/${OUTREACH_CAMPAIGN_ID}/outreach/preview-batch`);

  const inventory = review.email_inventory;
  expect(inventory.unique_email_ready_active).toBe(review.counts.email_ready);
  expect(inventory.active_unsent_email_ready).toBe(review.research_status.email_ready_before);
  expect(inventory.remaining_to_target).toBe(review.research_status.remaining_to_target);
  expect(inventory.approved_unsent).toBe(review.counts.approved_unsent);
  expect(inventory.unique_email_ready_active).not.toBe(25);
  expect(inventory.ready_to_send).toBeLessThanOrEqual(inventory.approved_unsent);

  expect(readiness.batch_preview.coverage.approved_leads).toBe(inventory.approved_unsent);
  expect(readiness.batch_preview.coverage.ready_to_send).toBe(
    readiness.batch_preview.email_inventory.ready_to_send,
  );
  expect(preview.coverage.approved_leads).toBe(inventory.approved_unsent);
  expect(preview.coverage.ready_to_send).toBe(preview.email_inventory.ready_to_send);
  expect(preview.email_inventory.unique_email_ready_active).toBe(inventory.unique_email_ready_active);
  expect(preview.email_inventory.remaining_to_target).toBe(inventory.remaining_to_target);

  await expect(page.locator('[data-voryx-research-status]')).toContainText(
    `Email-ready currently available: ${inventory.unique_email_ready_active}`,
  );
  await expect(page.locator('[data-voryx-research-status]')).toContainText(
    `Remaining needed: ${inventory.remaining_to_target}`,
  );

  expect(review.research_status.prospect_emails_sent || 0).toBe(0);
  expect(consoleErrors).toEqual([]);
  expect(failedRequests).toEqual([]);
});
