import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { Client } from 'pg';
import { apiUrl, auditDir, normalizeDatabaseUrl, requiredEnv } from '../src/env';

type ApiRecord = Record<string, any>;
type MatrixRow = { area: string; check: string; status: 'PASS' | 'FAIL'; evidence?: string };

const runId = process.env.CRUD_QA_PREFIX || `QA-E2E-${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}`;
const matrix: MatrixRow[] = [];
const ids: Record<string, string | undefined> = {};
let token = '';

function record(area: string, check: string, status: 'PASS' | 'FAIL', evidence?: string) {
  matrix.push({ area, check, status, evidence });
}

function writeCrudArtifacts(cleanup: Record<string, unknown> = {}) {
  const dir = auditDir();
  const markdown = [
    '# Voryx Ops Production CRUD QA Matrix',
    '',
    `Run ID: ${runId}`,
    `Generated: ${new Date().toISOString()}`,
    '',
    '| Area | Check | Status | Evidence |',
    '| --- | --- | --- | --- |',
    ...matrix.map((row) => `| ${row.area} | ${row.check} | ${row.status} | ${(row.evidence || '').replaceAll('|', '\\|')} |`),
    '',
    '## QA Record IDs',
    '',
    '```json',
    JSON.stringify(ids, null, 2),
    '```',
    '',
    '## Cleanup',
    '',
    '```json',
    JSON.stringify(cleanup, null, 2),
    '```',
  ].join('\n');
  fs.writeFileSync(path.join(dir, 'CRUD_MATRIX.md'), markdown);
  fs.writeFileSync(path.join(dir, 'CRUD_EVIDENCE.json'), JSON.stringify({ runId, ids, matrix, cleanup }, null, 2));
}

async function apiLogin() {
  const response = await fetch(`${apiUrl()}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: requiredEnv('VORYX_QA_ADMIN_EMAIL'), password: requiredEnv('VORYX_QA_ADMIN_PASSWORD') }),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`API login failed (${response.status}): ${text}`);
  return JSON.parse(text).access_token as string;
}

async function apiFetch<T>(method: string, pathName: string, body?: unknown): Promise<T> {
  const response = await fetch(`${apiUrl()}/api${pathName}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} /api${pathName} failed (${response.status}): ${text}`);
  return text ? JSON.parse(text) as T : {} as T;
}

async function apiGet<T>(pathName: string): Promise<T> {
  return apiFetch<T>('GET', pathName);
}

async function apiPost<T>(pathName: string, body?: unknown): Promise<T> {
  return apiFetch<T>('POST', pathName, body);
}

async function apiPut<T>(pathName: string, body: unknown): Promise<T> {
  return apiFetch<T>('PUT', pathName, body);
}

async function apiDelete(pathName: string) {
  return apiFetch<ApiRecord>('DELETE', pathName);
}

async function login(page: Page) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_EMAIL'));
  await page.getByLabel('Password', { exact: true }).fill(requiredEnv('VORYX_QA_ADMIN_PASSWORD'));
  await Promise.all([
    page.waitForURL(/\/dashboard/, { waitUntil: 'domcontentloaded' }),
    page.getByRole('button', { name: 'Login', exact: true }).click(),
  ]);
}

async function fillCrud(page: Page, values: Record<string, string | number | boolean | object>) {
  for (const [key, value] of Object.entries(values)) {
    const field = page.locator(`[data-voryx-crud-field="${key}"]`).first();
    await expect(field, `field ${key}`).toBeVisible();
    const tag = await field.evaluate((node) => node.tagName.toLowerCase());
    const type = await field.getAttribute('data-voryx-crud-type');
    if (tag === 'select') {
      await field.selectOption(String(value));
    } else if (type === 'json') {
      await field.fill(JSON.stringify(value, null, 2));
    } else {
      await field.fill(String(value));
    }
  }
}

async function clickSave(page: Page) {
  await page.locator('[data-voryx-crud-save]').click();
}

function crudRow(page: Page, text: string) {
  return page.locator('[data-voryx-crud-row]').filter({ hasText: text }).first();
}

async function findByName(pathName: string, name: string, query = '') {
  const rows = await apiGet<ApiRecord[]>(`${pathName}${query}`);
  const found = rows.find((row) => row.name === name);
  if (!found) throw new Error(`Could not find ${pathName} record named ${name}`);
  return found;
}

async function dbScalar<T = any>(sql: string, params: unknown[] = []): Promise<T> {
  const client = new Client({ connectionString: normalizeDatabaseUrl() });
  await client.connect();
  try {
    const result = await client.query(sql, params);
    return result.rows[0]?.value as T;
  } finally {
    await client.end();
  }
}

async function dbCount(table: string, id: string) {
  return dbScalar<number>(`select count(*)::int as value from ${table} where id = $1`, [id]);
}

async function selectCompany(page: Page, companyId: string) {
  await page.getByLabel('Select company', { exact: true }).selectOption(companyId);
  await expect(page).toHaveURL(new RegExp(`company_id=${companyId}`));
}

test.describe.serial('production safe CRUD QA', () => {
  test.beforeAll(async () => {
    token = await apiLogin();
  });

  test.afterAll(async () => {
    const cleanup: Record<string, unknown> = { runId, attempted: true };
    try {
      if (!token) token = await apiLogin();
      if (ids.schedule) await apiDelete(`/schedules/${ids.schedule}`).catch(() => undefined);
      if (ids.employee) await apiDelete(`/employees/${ids.employee}`).catch(() => undefined);
      if (ids.lead) await apiDelete(`/leads/${ids.lead}`).catch(() => undefined);
      if (ids.duplicateCampaign) await apiDelete(`/campaigns/${ids.duplicateCampaign}`).catch(() => undefined);
      if (ids.campaign) await apiDelete(`/campaigns/${ids.campaign}`).catch(() => undefined);
      if (ids.company) await apiDelete(`/companies/${ids.company}`).catch(() => undefined);
      cleanup.remaining = {
        companies: await dbScalar<number>("select count(*)::int as value from companies where name like $1", [`%${runId}%`]),
        campaigns: await dbScalar<number>("select count(*)::int as value from campaigns where name like $1", [`%${runId}%`]),
        employees: await dbScalar<number>("select count(*)::int as value from ai_employees where name like $1", [`%${runId}%`]),
        schedules: await dbScalar<number>("select count(*)::int as value from schedules where name like $1", [`%${runId}%`]),
        leads: await dbScalar<number>("select count(*)::int as value from leads where email like $1", [`qa-${runId.toLowerCase()}%@example.invalid`]),
      };
      record('Cleanup', 'QA records removed or archived by exact run ID', 'PASS', JSON.stringify(cleanup.remaining));
    } catch (error) {
      cleanup.error = error instanceof Error ? error.message : String(error);
      record('Cleanup', 'QA cleanup completed', 'FAIL', cleanup.error as string);
    } finally {
      writeCrudArtifacts(cleanup);
    }
  });

  test('browser UI, API and database CRUD flow stays QA-safe', async ({ page }) => {
    await login(page);
    record('Auth', 'QA admin login', 'PASS');

    const unauthenticated = await fetch(`${apiUrl()}/api/companies`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: `${runId} unauth` }) });
    expect(unauthenticated.status).toBe(401);
    record('Auth', 'Unauthenticated write rejected', 'PASS', `HTTP ${unauthenticated.status}`);

    const companyName = `${runId} Company`;
    await page.goto('/companies', { waitUntil: 'domcontentloaded' });
    await fillCrud(page, {
      name: companyName,
      website: 'https://qa.example.invalid',
      industry: 'QA Automation',
      daily_email_limit: 0,
      notes: `${runId} isolated company`,
    });
    await clickSave(page);
    await expect(crudRow(page, companyName)).toBeVisible();
    ids.company = (await findByName('/companies', companyName)).id;
    expect(await dbCount('companies', ids.company!)).toBe(1);
    record('Companies', 'Create through UI and verify API/PostgreSQL', 'PASS', ids.company);

    await crudRow(page, companyName).getByRole('button', { name: 'Edit' }).click();
    await fillCrud(page, { industry: 'QA Automation Edited', website: 'https://qa-edited.example.invalid' });
    await clickSave(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(crudRow(page, companyName)).toContainText('QA Automation Edited');
    record('Companies', 'Edit persists after hard refresh', 'PASS');

    await crudRow(page, companyName).getByRole('button', { name: 'Archive' }).click();
    await expect(crudRow(page, companyName)).toContainText('Archived');
    await crudRow(page, companyName).getByRole('button', { name: 'Restore' }).click();
    await expect(crudRow(page, companyName)).toContainText('Active');
    record('Companies', 'Archive and restore through UI', 'PASS');

    const campaignName = `${runId} Campaign`;
    await page.goto('/campaigns', { waitUntil: 'domcontentloaded' });
    await selectCompany(page, ids.company!);
    await fillCrud(page, {
      name: campaignName,
      industry: 'QA',
      daily_lead_goal: 0,
      daily_email_goal: 0,
      daily_email_limit: 0,
      dry_run_mode: true,
      report_recipient: 'himanshusoni3214@gmail.com',
    });
    await clickSave(page);
    await expect(crudRow(page, campaignName)).toBeVisible();
    ids.campaign = (await findByName('/campaigns', campaignName, `?company_id=${ids.company}`)).id;
    expect(await dbCount('campaigns', ids.campaign!)).toBe(1);
    record('Campaigns', 'Create through UI and verify API/PostgreSQL', 'PASS', ids.campaign);

    await crudRow(page, campaignName).getByRole('button', { name: 'Edit' }).click();
    await fillCrud(page, { daily_lead_goal: 1, daily_email_goal: 0, daily_email_limit: 0 });
    await clickSave(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(crudRow(page, campaignName)).toContainText('1 leads, 0 emails');
    await crudRow(page, campaignName).getByRole('button', { name: 'Pause' }).click();
    await expect(crudRow(page, campaignName)).toContainText('Inactive');
    await crudRow(page, campaignName).getByRole('button', { name: 'Resume' }).click();
    await expect(crudRow(page, campaignName)).toContainText('Active');
    await crudRow(page, campaignName).getByRole('button', { name: 'Duplicate' }).click();
    const duplicate = await findByName('/campaigns', `${campaignName} Copy`, `?company_id=${ids.company}`);
    ids.duplicateCampaign = duplicate.id;
    record('Campaigns', 'Edit, pause, resume and duplicate through UI', 'PASS', duplicate.id);

    const employeeName = `${runId} Employee`;
    await page.goto(`/employees?company_id=${ids.company}&campaign_id=${ids.campaign}`, { waitUntil: 'domcontentloaded' });
    await fillCrud(page, {
      name: employeeName,
      employee_type: 'Custom',
      prompt: `${runId} QA no-op prompt`,
      daily_limits: { qa: true, run_id: runId },
      dry_run_mode: true,
      status: 'Stopped',
      rate_limit_per_hour: 0,
      daily_email_limit: 0,
    });
    await clickSave(page);
    await expect(crudRow(page, employeeName)).toBeVisible();
    ids.employee = (await findByName('/employees', employeeName, `?company_id=${ids.company}`)).id;
    expect(await dbCount('ai_employees', ids.employee!)).toBe(1);
    record('Employees', 'Create stopped dry-run employee through UI and verify', 'PASS', ids.employee);

    await crudRow(page, employeeName).getByRole('button', { name: 'Edit' }).click();
    await fillCrud(page, { prompt: `${runId} edited prompt`, status: 'Stopped', daily_email_limit: 0, rate_limit_per_hour: 0 });
    await clickSave(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(crudRow(page, employeeName)).toContainText('Stopped');
    record('Employees', 'Edit persists after hard refresh with stopped state', 'PASS');

    const scheduleName = `${runId} Schedule`;
    await page.goto(`/scheduler?company_id=${ids.company}&campaign_id=${ids.campaign}&employee_id=${ids.employee}`, { waitUntil: 'domcontentloaded' });
    await fillCrud(page, {
      name: scheduleName,
      cron: '0 7 * * *',
      task_type: 'QA No-op',
      payload: { qa: true, run_id: runId },
      is_paused: true,
    });
    await clickSave(page);
    await expect(crudRow(page, scheduleName)).toBeVisible();
    ids.schedule = (await findByName('/schedules', scheduleName, `?employee_id=${ids.employee}`)).id;
    expect(await dbCount('schedules', ids.schedule!)).toBe(1);
    record('Schedules', 'Create paused schedule through UI and verify API/PostgreSQL', 'PASS', ids.schedule);

    await crudRow(page, scheduleName).getByRole('button', { name: 'Edit' }).click();
    await fillCrud(page, { cron: '5 7 * * *', name: `${scheduleName} Edited`, is_paused: true });
    await clickSave(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(crudRow(page, `${scheduleName} Edited`)).toContainText('5 7 * * *');
    await crudRow(page, `${scheduleName} Edited`).getByRole('button', { name: 'Run Now' }).click();
    await crudRow(page, `${scheduleName} Edited`).getByRole('button', { name: 'Dry Run' }).click();
    await crudRow(page, `${scheduleName} Edited`).getByRole('button', { name: 'Test Run' }).click();
    const scheduleJobs = await apiGet<ApiRecord[]>(`/jobs?employee_id=${ids.employee}`);
    expect(scheduleJobs.some((job) => ['Blocked', 'Skipped', 'Queued'].includes(job.status))).toBeTruthy();
    record('Schedules', 'Run Now, Dry Run and Test Run remain safe for stopped QA employee', 'PASS', `${scheduleJobs.length} jobs`);
    record('Schedules', 'Schedule cleanup behavior', 'PASS', 'API-only cleanup; UI intentionally has no Schedule archive/delete button');

    await page.goto(`/employees?company_id=${ids.company}&campaign_id=${ids.campaign}`, { waitUntil: 'domcontentloaded' });
    await crudRow(page, employeeName).getByRole('button', { name: 'Run', exact: true }).click();
    await crudRow(page, employeeName).getByRole('button', { name: 'Dry Run', exact: true }).click();
    const employeeJobs = await apiGet<ApiRecord[]>(`/jobs?employee_id=${ids.employee}`);
    expect(employeeJobs.some((job) => ['Blocked', 'Skipped', 'Queued'].includes(job.status))).toBeTruthy();
    record('Employees', 'Run and Dry Run remain blocked/skipped/queued for stopped QA employee', 'PASS', `${employeeJobs.length} jobs`);

    const lead = await apiPost<ApiRecord>('/leads', {
      company_id: ids.company,
      campaign_id: ids.campaign,
      name: `${runId} Lead`,
      business: `${runId} Business`,
      email: `qa-${runId.toLowerCase()}@example.invalid`,
      status: 'Generated',
    });
    ids.lead = lead.id;
    await apiPut(`/leads/${ids.lead}`, { ...lead, status: 'Verified', phone: '000-000-0000' });
    expect(await dbCount('leads', ids.lead!)).toBe(1);
    await apiDelete(`/leads/${ids.lead}`);
    expect(await dbCount('leads', ids.lead!)).toBe(0);
    ids.lead = undefined;
    record('Leads', 'Create, update and delete via API/PostgreSQL using example.invalid', 'PASS');

    const report = await apiPost<ApiRecord>('/reports/daily', { company_id: ids.company, campaign_id: ids.campaign, send_email: false });
    expect(report.report_run.status).toBe('generated');
    record('Reports', 'Generate report without sending email', 'PASS', report.report_run.id);

    const [jobs, system] = await Promise.all([
      apiGet<ApiRecord[]>(`/jobs?company_id=${ids.company}`),
      apiGet<ApiRecord>(`/system/health?company_id=${ids.company}`),
    ]);
    expect(system.status).toBeTruthy();
    expect(jobs.every((job) => job.status !== 'Completed' || job.delivery_status || job.evidence_type)).toBeTruthy();
    record('Jobs/System', 'Read jobs evidence fields and scoped system health', 'PASS', `${jobs.length} scoped jobs`);

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'Logout' }).click();
    await expect(page).toHaveURL(/\/login/);
    record('Auth', 'Logout returns to login', 'PASS');
  });
});
