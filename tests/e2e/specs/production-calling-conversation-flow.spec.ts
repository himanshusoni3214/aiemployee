import { expect, test, type Page } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { Client } from 'pg';
import { auditDir, requiredEnv } from '../src/env';


const CONSENTED_OPENING = 'Hi {{customer_name}}, this is Ava calling on behalf of Himanshu Soni, an Allstate Sales Agent in Scarborough. I’m following up on your permission to be contacted about auto or property insurance. Do you have thirty seconds?';
const BLOCKERS = [
  'Allstate caller-ID approval — Missing: approval decision',
  'Allstate recording/transcription approval — Missing: approval decision',
  'Allstate approved lead source — Missing: approval decision',
  'Allstate approved data-storage workflow — Missing: approval decision',
];


async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_EMAIL'));
  await page.getByLabel('Password', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_PASSWORD'));
  await Promise.all([
    page.waitForURL(/\/dashboard/, { waitUntil: 'domcontentloaded' }),
    page.getByRole('button', { name: 'Login', exact: true }).click(),
  ]);
}


test('Script Studio production workflow is published, recoverable and safe', async ({ page }) => {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  const profileName = `QA Script Studio Synthetic ${Date.now()}`;
  const screenshots = path.join(auditDir(), 'screenshots');
  const downloads = path.join(auditDir(), 'downloads');
  const syntheticCsv = path.join(auditDir(), 'synthetic-consent-preview.csv');
  await mkdir(screenshots, { recursive: true });
  await mkdir(downloads, { recursive: true });
  await writeFile(
    syntheticCsv,
    [
      'first_name,phone_number,consent_timestamp,consent_reference,product_interest,renewal_month,preferred_call_time,notes',
      'Synthetic QA,+14165550199,2026-07-26T18:10:00-04:00,QA-PREVIEW-ONLY-20260726,Auto and property insurance,October,weekday evening,Synthetic preview only - never call',
      '',
    ].join('\n'),
  );

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

  try {
    await login(page);
    await page.goto('/calling', { waitUntil: 'networkidle' });
    await expect(page.getByRole('heading', { name: 'Calling Channel Workspace' })).toBeVisible();
    await expect(page.getByText('Conversation Flow active').locator('..')).toContainText('Ready');
    await expect(page.getByText('Outbound number assigned').locator('..')).toContainText('Ready');
    await expect(page.getByText('Prospect calling disabled').locator('..')).toContainText('Ready');
    await expect(page.getByRole('button', { name: 'Place Conversation-Flow Internal Test Call' })).toBeDisabled();

    const studio = page.locator('section[data-call-script-studio]');
    await expect(studio.getByText('Live: v2', { exact: true })).toBeVisible();
    await expect(studio.getByText('Retell agent 2 / flow 2', { exact: true })).toBeVisible();
    await expect(studio.getByText('LIVE IN RETELL', { exact: true })).toBeVisible();
    await expect(studio.getByText('CURRENT DRAFT', { exact: true })).toBeVisible();
    await expect(studio.getByText('DRAFT PREVIEW', { exact: true })).toBeVisible();
    await expect(studio.getByText(CONSENTED_OPENING, { exact: true })).toHaveCount(3);
    await expect(studio.getByText('Exact Retell node text verified', { exact: true })).toBeVisible();
    await expect(studio.getByRole('button', { name: 'Save and test', exact: true })).toBeEnabled();
    await expect(studio.getByRole('button', { name: 'Publish', exact: true })).toBeDisabled();
    await expect(page.getByRole('heading', { name: 'LIVE RETELL PREVIEW' })).toBeVisible();
    await page.screenshot({ path: path.join(screenshots, 'script-studio-live-v2.png'), fullPage: true });

    await page.reload({ waitUntil: 'networkidle' });
    const reloadedStudio = page.locator('section[data-call-script-studio]');
    await expect(reloadedStudio.getByText('Live: v2', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText(CONSENTED_OPENING, { exact: true })).toHaveCount(3);

    await reloadedStudio.getByRole('tab', { name: 'Compliance', exact: true }).click();
    await expect(reloadedStudio.getByText('4 mandatory compliance items remain incomplete. Prospect calls are blocked.', { exact: true })).toBeVisible();
    for (const blocker of BLOCKERS) {
      await expect(reloadedStudio.getByText(blocker, { exact: true })).toBeVisible();
    }
    await expect(reloadedStudio.getByRole('checkbox', { name: 'Show only incomplete' })).toBeChecked();
    await expect(reloadedStudio.locator('table.ops-table tbody tr')).toHaveCount(4);
    await expect(reloadedStudio.getByText('Allstate approval package', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText('DNCL package', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText('Voryx system checks', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText('Lead-level checks', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText('Verified', { exact: true })).toHaveCount(6);
    await page.screenshot({ path: path.join(screenshots, 'compliance-four-blockers.png'), fullPage: true });

    await reloadedStudio.getByRole('tab', { name: 'Consented leads', exact: true }).click();
    await reloadedStudio.getByLabel('Source name', { exact: true }).fill(profileName);
    await reloadedStudio.getByLabel('Consent proof method', { exact: true }).fill('Synthetic CSV preview reference only');
    await reloadedStudio.getByLabel('Exact approved consent language', { exact: true }).fill('QA synthetic consent language. Not approved for live calling.');
    await reloadedStudio.getByLabel('Source approval evidence', { exact: true }).fill('QA synthetic preview only; deleted after validation');
    await reloadedStudio.getByLabel('Approval date', { exact: true }).fill('2026-07-26T18:10');
    await reloadedStudio.getByRole('checkbox', { name: 'Organization authorized', exact: true }).check();
    await reloadedStudio.getByRole('checkbox', { name: 'Automated/synthesized-call permission', exact: true }).check();
    await reloadedStudio.getByRole('button', { name: 'Save Consent Source Profile', exact: true }).click();
    await expect(reloadedStudio.getByText('Consent Source Profile saved and selected.', { exact: true })).toBeVisible();

    const simpleDownloadPromise = page.waitForEvent('download');
    await reloadedStudio.getByRole('button', { name: 'Download simple CSV template', exact: true }).click();
    const simpleDownload = await simpleDownloadPromise;
    const simplePath = path.join(downloads, 'allstate-consented-leads-simple.csv');
    await simpleDownload.saveAs(simplePath);
    expect(await readFile(simplePath, 'utf8')).toContain('first_name,phone_number,consent_timestamp,consent_reference');

    const advancedDownloadPromise = page.waitForEvent('download');
    await reloadedStudio.getByRole('button', { name: 'Download advanced CSV template', exact: true }).click();
    const advancedDownload = await advancedDownloadPromise;
    const advancedPath = path.join(downloads, 'allstate-consented-leads-advanced.csv');
    await advancedDownload.saveAs(advancedPath);
    expect(await readFile(advancedPath, 'utf8')).toContain('automated_or_synthesized_call_consent');

    await reloadedStudio.locator('input[type="file"]').setInputFiles(syntheticCsv);
    await expect(reloadedStudio.getByText('Total 1', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText('Valid 1', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText('Needs review 0', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByText('Duplicates 0', { exact: true })).toBeVisible();
    await expect(reloadedStudio.getByRole('button', { name: 'Import valid rows', exact: true })).toBeEnabled();
    await page.screenshot({ path: path.join(screenshots, 'consent-csv-preview.png'), fullPage: true });

    expect(consoleErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
  } finally {
    const client = new Client({ connectionString: requiredEnv('QA_DATABASE_URL') });
    await client.connect();
    await client.query('DELETE FROM consent_source_profiles WHERE name = $1', [profileName]);
    await client.end();
  }
});
