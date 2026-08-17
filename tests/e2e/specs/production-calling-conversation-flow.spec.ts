import { expect, test, type Page } from '@playwright/test';
import { createHmac } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { Client } from 'pg';
import { auditDir, requiredEnv } from '../src/env';


const runId = `QA-CALLING-${Date.now()}`;
const profileName = 'Allstate approved web leads';
const qaPhones = ['+16475550101', '+14165550102', '+12125550103'];


async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_EMAIL'));
  await page.getByLabel('Password', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_PASSWORD'));
  await Promise.all([
    page.waitForURL(/\/dashboard/, { waitUntil: 'domcontentloaded' }),
    page.getByRole('button', { name: 'Login', exact: true }).click(),
  ]);
}


async function browserApi(page: Page, method: string, pathName: string, body?: unknown) {
  return page.evaluate(async ({ methodName, apiPath, apiBody }) => {
    const token = window.localStorage.getItem('token');
    if (!token) throw new Error('Missing authenticated browser token');
    const response = await fetch(`/api${apiPath}`, {
      method: methodName,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: apiBody === undefined ? undefined : JSON.stringify(apiBody),
      cache: 'no-store',
    });
    const text = await response.text();
    if (!response.ok) throw new Error(`${methodName} /api${apiPath} failed (${response.status}): ${text}`);
    return text ? JSON.parse(text) : {};
  }, { methodName: method, apiPath: pathName, apiBody: body });
}


test('Allstate calling product completes no-call production workflow', async ({ page, request }) => {
  const consoleErrors: string[] = [];
  const failedRequests: string[] = [];
  const screenshots = path.join(auditDir(), 'screenshots');
  const downloads = path.join(auditDir(), 'downloads');
  const syntheticCsv = path.join(auditDir(), `${runId}.csv`);
  const ids: { profile?: string; batch?: string; lead?: string; queue?: string; attempts: string[]; outcomeQueues: string[]; providerCalls: string[] } = { attempts: [], outcomeQueues: [], providerCalls: [] };
  let attemptsBefore = 0;
  await mkdir(screenshots, { recursive: true });
  await mkdir(downloads, { recursive: true });
  await writeFile(syntheticCsv, [
    'first_name,phone_number,consent_timestamp,consent_reference,product_interest,renewal_month,preferred_call_time,timezone,notes,is_test',
    `Ready QA,647-555-0101,2026-08-08T12:00:00-04:00,${runId}-READY,Auto and property insurance,October,weekday evening,America/Toronto,${runId},true`,
    `Duplicate QA,(647) 555-0101,2026-08-08T12:00:00-04:00,${runId}-DUPLICATE,Auto and property insurance,,,,${runId},true`,
    `US QA,212-555-0103,2026-08-08T12:00:00-04:00,${runId}-US,Auto and property insurance,,,,${runId},true`,
    `Review QA,416-555-0102,2026-08-08T12:00:00-04:00,,Auto and property insurance,,,,${runId},true`,
    '',
  ].join('\n'));

  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText || '';
    const url = new URL(request.url());
    const benignAbort = request.method() === 'GET' && failure.includes('ERR_ABORTED') && (url.searchParams.has('_rsc') || request.resourceType() === 'document');
    if (!benignAbort) failedRequests.push(`${request.method()} ${request.url()} ${failure}`.trim());
  });
  page.on('response', (response) => {
    if (response.status() >= 400 && !response.url().endsWith('/favicon.ico')) failedRequests.push(`${response.status()} ${response.url()}`);
  });

  const client = new Client({ connectionString: requiredEnv('QA_DATABASE_URL') });
  await client.connect();
  try {
    attemptsBefore = Number((await client.query("select count(*)::int as count from call_attempts where campaign_id = 'campaign-allstate-quote-calling' and internal_test = false")).rows[0].count);
    await login(page);

    await page.goto('/calling', { waitUntil: 'networkidle' });
    await expect(page.getByRole('heading', { name: 'AI Calling', exact: true })).toBeVisible();
    await expect(page.getByText('Connected', { exact: true })).toBeVisible();
    await expect(page.getByText('+1 437-747-5010', { exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(screenshots, 'calling-overview.png'), fullPage: true });

    await page.getByRole('button', { name: 'Settings', exact: true }).click();
    await page.locator('select[data-voryx-source-preset]').selectOption('allstate_web');
    await page.locator('select[data-voryx-consent-wording-preset]').selectOption('web_form');
    await page.locator('select[data-voryx-proof-preset]').selectOption('web_form');
    await page.locator('select[data-voryx-evidence-preset]').selectOption('consent_reference');
    await page.getByLabel('Organization is authorized', { exact: true }).check();
    await page.getByLabel('Automated/synthesized calls are permitted', { exact: true }).check();
    await page.getByRole('button', { name: 'Save Consent Source', exact: true }).click();
    await expect(page.getByText('Consent Source saved.', { exact: true })).toBeVisible();
    const savedProfile = (await client.query('select id, approved_consent_language, consent_proof_method, source_approval_evidence from consent_source_profiles where name = $1 order by created_at desc limit 1', [profileName])).rows[0];
    expect(savedProfile.approved_consent_language).toContain('automated or synthesized calls');
    expect(savedProfile.consent_proof_method).toContain('Web form submission');
    expect(savedProfile.source_approval_evidence).toContain('consent_reference');
    ids.profile = savedProfile.id;
    await page.screenshot({ path: path.join(screenshots, 'calling-consent-source-presets.png'), fullPage: true });

    await page.getByRole('button', { name: 'Contacts', exact: true }).click();
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download Template', exact: true }).click();
    const download = await downloadPromise;
    const templatePath = path.join(downloads, 'allstate-calling-contacts.csv');
    await download.saveAs(templatePath);
    expect((await readFile(templatePath, 'utf8')).split('\n')[0].trim()).toBe('first_name,phone_number,consent_timestamp,consent_reference,last_name,product_interest,renewal_month,preferred_call_time,timezone,notes');

    const consentSource = page.locator('select[data-voryx-consent-source]');
    await expect(consentSource).toBeVisible();
    if (await consentSource.inputValue() !== ids.profile) await consentSource.selectOption(ids.profile!);
    await expect(consentSource).toHaveValue(ids.profile!);
    await page.getByLabel('CSV file', { exact: true }).setInputFiles(syntheticCsv);
    await page.getByRole('button', { name: 'Upload Contacts', exact: true }).click();
    await expect(page.getByText('Contacts uploaded and validated.', { exact: true })).toBeVisible();
    await expect(page.locator('[data-voryx-import-review]')).toContainText('Uploaded4');
    await expect(page.locator('[data-voryx-import-review]')).toContainText('Ready for AI call1');
    await expect(page.locator('[data-voryx-import-review]')).toContainText('Needs review1');
    await expect(page.locator('[data-voryx-import-review]')).toContainText('Blocked2');
    await page.screenshot({ path: path.join(screenshots, 'calling-contact-review.png'), fullPage: true });

    const workspace = await browserApi(page, 'GET', '/calling/allstate');
    ids.batch = workspace.latest_import.id;
    const qaLead = (workspace.script_studio.consented_leads as any[]).find((item) => item.is_test && item.consent_reference === `${runId}-READY`);
    expect(qaLead).toBeTruthy();
    ids.lead = qaLead.id;
    const dbLead = (await client.query('select phone_number, is_test from consented_calling_leads where id = $1', [ids.lead])).rows[0];
    expect(dbLead.phone_number).toBe('+16475550101');
    expect(dbLead.is_test).toBe(true);

    await page.getByRole('button', { name: 'DRY RUN MY CONTACTS', exact: true }).click();
    await expect(page.getByText('Dry run completed. No calls were placed.', { exact: true })).toBeVisible();
    await expect(page.getByText('No telephone activity occurred.', { exact: true })).toBeVisible();

    const started = await browserApi(page, 'POST', '/calling/allstate/campaign/start', {
      confirmation: 'START APPROVED CALLING CAMPAIGN',
      execution_mode: 'mock',
      defer_mock_seconds: 3600,
    });
    expect(started.status).toBe('running');
    const queueRow = (await client.query('select id, status, execution_mode from call_queue_items where canonical_lead_id = $1', [ids.lead])).rows[0];
    ids.queue = queueRow.id;
    expect(queueRow.status).toBe('queued');
    expect(queueRow.execution_mode).toBe('mock');

    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Calling', exact: true }).click();
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    await expect(page.getByText('Campaign pause succeeded.', { exact: true })).toBeVisible();
    expect((await client.query('select campaign_status from call_campaign_settings where campaign_id = $1', ['campaign-allstate-quote-calling'])).rows[0].campaign_status).toBe('paused');
    await page.getByRole('button', { name: 'Resume', exact: true }).click();
    await expect(page.getByText('Campaign resume succeeded.', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Stop', exact: true }).click();
    await expect(page.getByText('Campaign stop succeeded.', { exact: true })).toBeVisible();
    await page.screenshot({ path: path.join(screenshots, 'calling-controls-stopped.png'), fullPage: true });

    await page.getByRole('button', { name: 'Results', exact: true }).click();
    await expect(page.locator('[data-voryx-call-results]')).toContainText('Ready QA');
    await expect(page.locator('[data-voryx-call-results]')).toContainText('Cancelled');
    await page.getByRole('button', { name: 'View', exact: true }).first().click();
    await expect(page.getByRole('dialog', { name: 'Result details' })).toBeVisible();
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    const scriptId = (await client.query("select id from call_script_versions where version_number = 8 and status = 'published' order by created_at desc limit 1")).rows[0].id;
    const createOutcomeAttempt = async (kind: string) => {
      const attemptId = `${runId}-${kind}-attempt`;
      const queueId = `${runId}-${kind}-queue`;
      const providerCallId = `${runId}-${kind}-provider`;
      await client.query(`insert into call_attempts (
        id, company_id, campaign_id, consented_calling_lead_id, provider, provider_call_id,
        provider_agent_id, provider_agent_version, from_number, to_number, mode, status,
        requested_at, provider_receipt, metadata_json, internal_test, created_at, updated_at,
        provider_cost_final, provider_cost_currency, provider_cost_breakdown, script_version_id
      ) values ($1, 'company-allstate-himanshu', 'campaign-allstate-quote-calling', $2, 'mock', $3,
        'agent_e3970346853be2a0fbbb0ec0e6', 8, '+14377475010', '+16475550101', 'consented_campaign', 'initiated',
        now(), '{}'::json, $4::json, true, now(), now(), false, 'USD', '{}'::json, $5)`,
      [attemptId, ids.lead, providerCallId, JSON.stringify({ qa_run_id: runId, no_call: true }), scriptId]);
      await client.query(`insert into call_queue_items (
        id, company_id, campaign_id, canonical_lead_id, phone_number, dedupe_key,
        script_version_id, script_version, provider_agent_id, provider_agent_version,
        consent_snapshot, status, priority, attempts, provider_call_id, call_attempt_id,
        execution_mode, created_at, started_at, updated_at
      ) values ($1, 'company-allstate-himanshu', 'campaign-allstate-quote-calling', $2, '+16475550101', $3,
        $4, 8, 'agent_e3970346853be2a0fbbb0ec0e6', 8, '{}'::json, 'calling', 100, 1, $5, $6,
        'mock', now(), now(), now())`,
      [queueId, ids.lead, `${runId}:${kind}`, scriptId, providerCallId, attemptId]);
      ids.attempts.push(attemptId); ids.outcomeQueues.push(queueId); ids.providerCalls.push(providerCallId);
      return { attemptId, queueId, providerCallId };
    };

    const callback = await createOutcomeAttempt('callback');
    const callbackPayload = JSON.stringify({
      event: 'call_ended',
      call: {
        call_id: callback.providerCallId, call_status: 'ended', agent_id: 'agent_e3970346853be2a0fbbb0ec0e6', agent_version: 8,
        from_number: '+14377475010', to_number: '+16475550101', end_timestamp: Date.now(), duration_ms: 30000,
        call_analysis: { custom_analysis_data: {
          call_outcome: 'callback', callback_requested: true, callback_date: '2026-08-15', callback_time: '10:00',
          callback_timezone: 'America/Toronto', callback_consent: true, callback_reason: 'Renewal follow-up', renewal_month: 'October',
        } },
      },
    });
    const signature = createHmac('sha256', requiredEnv('RETELL_WEBHOOK_API_KEY')).update(callbackPayload).digest('hex');
    const callbackResponse = await request.post('/api/webhooks/retell', { data: callbackPayload, headers: { 'Content-Type': 'application/json', 'X-Retell-Signature': signature } });
    expect(callbackResponse.ok()).toBe(true);
    expect((await client.query('select status, callback_consent from call_queue_items where id = $1', [callback.queueId])).rows[0]).toMatchObject({ status: 'callback', callback_consent: true });

    const appointment = await createOutcomeAttempt('appointment');
    const appointmentResponse = await request.post('/api/retell/tools/book-quote-appointment', {
      headers: { 'X-Voryx-Retell-Tool-Token': requiredEnv('RETELL_TOOL_TOKEN') },
      data: { voryx_call_attempt_id: appointment.attemptId, appointment_date: '2026-08-18', appointment_time: '18:30', timezone: 'America/Toronto', insurance_interest: 'Auto and property insurance' },
    });
    expect(appointmentResponse.ok()).toBe(true);
    expect((await client.query('select status from call_queue_items where id = $1', [appointment.queueId])).rows[0].status).toBe('appointment');

    const dnc = await createOutcomeAttempt('dnc');
    const dncResponse = await request.post('/api/retell/tools/mark-do-not-call', {
      headers: { 'X-Voryx-Retell-Tool-Token': requiredEnv('RETELL_TOOL_TOKEN') },
      data: { voryx_call_attempt_id: dnc.attemptId, phone_number: '+16475550101', reason: 'Synthetic QA do not call' },
    });
    expect(dncResponse.ok()).toBe(true);
    expect((await client.query('select consent_withdrawn from consented_calling_leads where id = $1', [ids.lead])).rows[0].consent_withdrawn).toBe(true);

    await page.reload({ waitUntil: 'networkidle' });
    await page.getByRole('button', { name: 'Results', exact: true }).click();
    const results = page.locator('[data-voryx-call-results]');
    await expect(results).toContainText('Callback');
    await expect(results).toContainText('Appointment');
    await expect(results).toContainText('Dnc');
    await page.screenshot({ path: path.join(screenshots, 'calling-results.png'), fullPage: true });

    const attemptsAfter = Number((await client.query("select count(*)::int as count from call_attempts where campaign_id = 'campaign-allstate-quote-calling' and internal_test = false")).rows[0].count);
    expect(attemptsAfter).toBe(attemptsBefore);
    expect(consoleErrors).toEqual([]);
    expect(failedRequests).toEqual([]);

    await writeFile(path.join(auditDir(), 'CALLING_PRODUCT_EVIDENCE.json'), JSON.stringify({
      runId, ids, attemptsBefore, attemptsAfter, realCallsPlaced: 0,
      checks: ['template', 'upload', 'normalization', 'duplicate', 'missing consent', 'non-Canadian block', 'dry run', 'mock queue', 'pause', 'resume', 'stop', 'results', 'callback', 'renewal callback', 'appointment', 'dnc'],
    }, null, 2));
  } finally {
    await client.query('begin');
    try {
      if (ids.lead) await client.query("delete from call_queue_items where canonical_lead_id = $1 and execution_mode = 'mock'", [ids.lead]);
      await client.query('delete from call_queue_items where dedupe_key like $1', [`${runId}:%`]);
      await client.query('delete from call_appointments where call_attempt_id = any($1::varchar[])', [ids.attempts]);
      await client.query('delete from call_dispositions where call_attempt_id = any($1::varchar[])', [ids.attempts]);
      await client.query('delete from call_transcripts where call_attempt_id = any($1::varchar[])', [ids.attempts]);
      await client.query('delete from retell_webhook_events where provider_call_id = any($1::varchar[])', [ids.providerCalls]);
      await client.query('delete from call_attempts where id = any($1::varchar[]) and internal_test = true', [ids.attempts]);
      await client.query("delete from suppression_entries where value = '+16475550101' and reason = 'Synthetic QA do not call'");
      if (ids.queue) await client.query('delete from call_queue_items where id = $1 and execution_mode = $2', [ids.queue, 'mock']);
      if (ids.batch) {
        await client.query('delete from call_contact_import_rows where batch_id = $1 and is_test = true', [ids.batch]);
        await client.query('delete from call_contact_import_batches where id = $1 and filename = $2', [ids.batch, `${runId}.csv`]);
      }
      if (ids.lead) await client.query('delete from consented_calling_leads where id = $1 and is_test = true', [ids.lead]);
      if (ids.profile) await client.query('delete from consent_source_profiles where id = $1 and name = $2', [ids.profile, profileName]);
      await client.query("update call_campaign_settings set campaign_status = 'not_started', prospect_calling_enabled = false, automated_queue_enabled = false where campaign_id = 'campaign-allstate-quote-calling'");
      await client.query('commit');
    } catch (error) {
      await client.query('rollback');
      throw error;
    }
    await client.end();
  }
});
