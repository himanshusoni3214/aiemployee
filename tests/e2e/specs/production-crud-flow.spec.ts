import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { Client } from 'pg';
import { BREW_COMPANY_ID, EXPECTED_COUNTS, apiUrl, auditDir, normalizeDatabaseUrl, requiredEnv } from '../src/env';

type ApiRecord = Record<string, any>;
type MatrixRow = { area: string; check: string; status: 'PASS' | 'FAIL'; evidence?: string };

const runId = process.env.CRUD_QA_PREFIX || `QA-E2E-${new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)}`;
const matrix: MatrixRow[] = [];
const ids: Record<string, string | undefined> = {};
const leadEmail = `qa-${runId.toLowerCase()}@example.invalid`;
let brewCountsBefore: Record<string, number> | null = null;
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

async function apiPostRaw(pathName: string, body?: unknown) {
  const response = await fetch(`${apiUrl()}/api${pathName}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let json: any = {};
  try {
    json = text ? JSON.parse(text) : {};
  } catch {
    json = { raw: text };
  }
  return { status: response.status, body: json };
}

async function apiPut<T>(pathName: string, body: unknown): Promise<T> {
  return apiFetch<T>('PUT', pathName, body);
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

async function dbRow<T = ApiRecord>(sql: string, params: unknown[] = []): Promise<T | null> {
  const client = new Client({ connectionString: normalizeDatabaseUrl() });
  await client.connect();
  try {
    const result = await client.query(sql, params);
    return (result.rows[0] || null) as T | null;
  } finally {
    await client.end();
  }
}

async function brewCounts() {
  const client = new Client({ connectionString: normalizeDatabaseUrl() });
  await client.connect();
  try {
    const [campaigns, employees, schedules] = await Promise.all([
      client.query("select count(*)::int as value from campaigns where company_id = $1 and lower(status::text) <> 'archived'", [BREW_COMPANY_ID]),
      client.query("select count(*)::int as value from ai_employees where company_id = $1 and lower(status::text) <> 'archived'", [BREW_COMPANY_ID]),
      client.query(
        "select count(*)::int as value from schedules s join ai_employees e on e.id = s.employee_id where e.company_id = $1 and lower(e.status::text) <> 'archived'",
        [BREW_COMPANY_ID],
      ),
    ]);
    return {
      campaigns: campaigns.rows[0].value as number,
      employees: employees.rows[0].value as number,
      schedules: schedules.rows[0].value as number,
    };
  } finally {
    await client.end();
  }
}

function assertSafeRunId() {
  if (!runId.startsWith('QA-E2E-')) throw new Error(`Refusing destructive cleanup for unsafe run ID ${runId}`);
}

async function cleanupExactQaIds() {
  assertSafeRunId();
  const cleanup: Record<string, unknown> = { runId, attempted: true, deleted: {}, remaining: {}, validated: {} };
  const client = new Client({ connectionString: normalizeDatabaseUrl() });
  await client.connect();
  try {
    await client.query('begin');

    async function row(table: string, id?: string) {
      if (!id) return null;
      const result = await client.query(`select * from ${table} where id = $1 for update`, [id]);
      return result.rows[0] || null;
    }

    const company = await row('companies', ids.company);
    if (company && (!String(company.name).startsWith(runId) || String(company.name).toLowerCase() === 'brew it by sash')) {
      throw new Error(`Refusing to delete company ${ids.company}; name ${company.name} does not match current QA run`);
    }
    const campaign = await row('campaigns', ids.campaign);
    if (campaign && (!String(campaign.name).startsWith(runId) || campaign.company_id !== ids.company)) {
      throw new Error(`Refusing to delete campaign ${ids.campaign}; it is not scoped to ${runId}`);
    }
    const duplicateCampaign = await row('campaigns', ids.duplicateCampaign);
    if (duplicateCampaign && (!String(duplicateCampaign.name).startsWith(runId) || duplicateCampaign.company_id !== ids.company)) {
      throw new Error(`Refusing to delete duplicate campaign ${ids.duplicateCampaign}; it is not scoped to ${runId}`);
    }
    const employee = await row('ai_employees', ids.employee);
    if (employee && (!String(employee.name).startsWith(runId) || employee.company_id !== ids.company)) {
      throw new Error(`Refusing to delete employee ${ids.employee}; it is not scoped to ${runId}`);
    }
    const schedule = await row('schedules', ids.schedule);
    if (schedule && (!String(schedule.name).startsWith(runId) || schedule.employee_id !== ids.employee)) {
      throw new Error(`Refusing to delete schedule ${ids.schedule}; it is not scoped to ${runId}`);
    }
    const lead = await row('leads', ids.lead);
    if (lead && (lead.email !== leadEmail || lead.company_id !== ids.company)) {
      throw new Error(`Refusing to delete lead ${ids.lead}; it is not scoped to ${runId}`);
    }
    const reportRun = await row('report_runs', ids.reportRun);
    if (reportRun && (reportRun.company_id !== ids.company || reportRun.campaign_id !== ids.campaign)) {
      throw new Error(`Refusing to delete report run ${ids.reportRun}; it is not scoped to ${runId}`);
    }
    cleanup.validated = {
      company: Boolean(company),
      campaign: Boolean(campaign),
      duplicateCampaign: Boolean(duplicateCampaign),
      employee: Boolean(employee),
      schedule: Boolean(schedule),
      lead: Boolean(lead),
      reportRun: Boolean(reportRun),
    };

    const campaignIds = [ids.campaign, ids.duplicateCampaign].filter(Boolean) as string[];
    const jobDelete = await client.query(
      `delete from jobs
       where ($1::text is not null and employee_id = $1)
          or (cardinality($2::text[]) > 0 and campaign_id = any($2::text[]))
       returning id`,
      [ids.employee || null, campaignIds],
    );
    const reportDelete = await client.query(
      `delete from report_runs
       where ($1::text is not null and company_id = $1)
          or (cardinality($2::text[]) > 0 and campaign_id = any($2::text[]))
       returning id`,
      [ids.company || null, campaignIds],
    );
    const activityDelete = await client.query(
      `delete from activity_logs
       where ($1::text is not null and company_id = $1)
          or entity_id = any($2::text[])
       returning id`,
      [ids.company || null, [ids.company, ids.campaign, ids.duplicateCampaign, ids.employee, ids.schedule, ids.lead].filter(Boolean)],
    );
    const scheduleDelete = ids.schedule ? await client.query('delete from schedules where id = $1 returning id', [ids.schedule]) : { rows: [] };
    const leadDelete = ids.lead ? await client.query('delete from leads where id = $1 returning id', [ids.lead]) : { rows: [] };
    const employeeDelete = ids.employee ? await client.query('delete from ai_employees where id = $1 returning id', [ids.employee]) : { rows: [] };
    const duplicateDelete = ids.duplicateCampaign ? await client.query('delete from campaigns where id = $1 returning id', [ids.duplicateCampaign]) : { rows: [] };
    const campaignDelete = ids.campaign ? await client.query('delete from campaigns where id = $1 returning id', [ids.campaign]) : { rows: [] };
    const companyDelete = ids.company ? await client.query('delete from companies where id = $1 returning id', [ids.company]) : { rows: [] };

    cleanup.deleted = {
      jobs: jobDelete.rows.map((item) => item.id),
      report_runs: reportDelete.rows.map((item) => item.id),
      activity_logs: activityDelete.rows.map((item) => item.id),
      schedules: scheduleDelete.rows.map((item) => item.id),
      leads: leadDelete.rows.map((item) => item.id),
      employees: employeeDelete.rows.map((item) => item.id),
      duplicate_campaigns: duplicateDelete.rows.map((item) => item.id),
      campaigns: campaignDelete.rows.map((item) => item.id),
      companies: companyDelete.rows.map((item) => item.id),
    };

    const remaining = {
      companies: ids.company ? Number((await client.query('select count(*)::int as value from companies where id = $1', [ids.company])).rows[0].value) : 0,
      campaigns: campaignIds.length ? Number((await client.query('select count(*)::int as value from campaigns where id = any($1::text[])', [campaignIds])).rows[0].value) : 0,
      employees: ids.employee ? Number((await client.query('select count(*)::int as value from ai_employees where id = $1', [ids.employee])).rows[0].value) : 0,
      schedules: ids.schedule ? Number((await client.query('select count(*)::int as value from schedules where id = $1', [ids.schedule])).rows[0].value) : 0,
      leads: ids.lead ? Number((await client.query('select count(*)::int as value from leads where id = $1', [ids.lead])).rows[0].value) : 0,
      report_run_id: ids.reportRun ? Number((await client.query('select count(*)::int as value from report_runs where id = $1', [ids.reportRun])).rows[0].value) : 0,
      jobs: Number((await client.query(
        `select count(*)::int as value from jobs
         where ($1::text is not null and employee_id = $1)
            or (cardinality($2::text[]) > 0 and campaign_id = any($2::text[]))`,
        [ids.employee || null, campaignIds],
      )).rows[0].value),
      report_runs: Number((await client.query(
        `select count(*)::int as value from report_runs
         where ($1::text is not null and company_id = $1)
            or (cardinality($2::text[]) > 0 and campaign_id = any($2::text[]))`,
        [ids.company || null, campaignIds],
      )).rows[0].value),
    };
    cleanup.remaining = remaining;
    const leftovers = Object.values(remaining).reduce((sum, value) => sum + Number(value), 0);
    if (leftovers !== 0) throw new Error(`QA cleanup left ${leftovers} exact current-run records`);

    const afterBrewCounts = {
      campaigns: Number((await client.query("select count(*)::int as value from campaigns where company_id = $1 and lower(status::text) <> 'archived'", [BREW_COMPANY_ID])).rows[0].value),
      employees: Number((await client.query("select count(*)::int as value from ai_employees where company_id = $1 and lower(status::text) <> 'archived'", [BREW_COMPANY_ID])).rows[0].value),
      schedules: Number((await client.query(
        "select count(*)::int as value from schedules s join ai_employees e on e.id = s.employee_id where e.company_id = $1 and lower(e.status::text) <> 'archived'",
        [BREW_COMPANY_ID],
      )).rows[0].value),
    };
    cleanup.brewCountsBefore = brewCountsBefore;
    cleanup.brewCountsAfter = afterBrewCounts;
    expect(afterBrewCounts).toEqual(brewCountsBefore || EXPECTED_COUNTS);

    await client.query('commit');
    cleanup.committed = true;
    return cleanup;
  } catch (error) {
    await client.query('rollback').catch(() => undefined);
    cleanup.error = error instanceof Error ? error.message : String(error);
    throw Object.assign(error instanceof Error ? error : new Error(String(error)), { cleanup });
  } finally {
    await client.end();
    fs.writeFileSync(path.join(auditDir(), 'CLEANUP_EVIDENCE.json'), JSON.stringify(cleanup, null, 2));
  }
}

async function selectCompany(page: Page, companyId: string) {
  await page.getByLabel('Select company', { exact: true }).selectOption(companyId);
  await expect(page).toHaveURL(new RegExp(`company_id=${companyId}`));
}

test.describe.serial('production safe CRUD QA', () => {
  test.beforeAll(async () => {
    token = await apiLogin();
    brewCountsBefore = await brewCounts();
    expect(brewCountsBefore).toEqual(EXPECTED_COUNTS);
  });

  test.afterAll(async () => {
    let cleanup: Record<string, unknown> = { runId, attempted: true };
    let cleanupFailure: Error | null = null;
    try {
      if (!token) token = await apiLogin();
      cleanup = await cleanupExactQaIds();
      record('Cleanup', 'Exact current-run QA IDs removed from PostgreSQL', 'PASS', JSON.stringify(cleanup.remaining));
    } catch (error) {
      cleanupFailure = error instanceof Error ? error : new Error(String(error));
      cleanup = (error as any)?.cleanup || cleanup;
      cleanup.error = cleanupFailure.message;
      record('Cleanup', 'QA cleanup completed', 'FAIL', cleanup.error as string);
    } finally {
      writeCrudArtifacts(cleanup);
    }
    if (cleanupFailure) throw cleanupFailure;
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
      timezone: 'America/Toronto',
      default_report_recipient: 'himanshusoni3214@gmail.com',
      notes: `${runId} isolated company`,
      status: 'Active',
    });
    await clickSave(page);
    await expect(crudRow(page, companyName)).toBeVisible();
    ids.company = (await findByName('/companies', companyName)).id;
    expect(await dbCount('companies', ids.company!)).toBe(1);
    record('Companies', 'Create through UI and verify API/PostgreSQL', 'PASS', ids.company);

    let companyPutRequests = 0;
    const companyRequestCounter = (request: any) => {
      const url = new URL(request.url());
      if (request.method() === 'PUT' && url.pathname === `/api/companies/${ids.company}`) companyPutRequests += 1;
    };
    page.on('request', companyRequestCounter);
    await crudRow(page, companyName).getByRole('button', { name: 'Edit' }).click();
    await fillCrud(page, { industry: 'QA Automation Edited', website: 'https://qa-edited.example.invalid' });
    await clickSave(page);
    page.off('request', companyRequestCounter);
    expect(companyPutRequests).toBe(1);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(crudRow(page, companyName)).toContainText('QA Automation Edited');
    const companyAfterEdit = await apiGet<ApiRecord[]>('/companies').then((rows) => rows.find((row) => row.id === ids.company));
    expect(companyAfterEdit).toMatchObject({
      name: companyName,
      website: 'https://qa-edited.example.invalid',
      industry: 'QA Automation Edited',
      daily_email_limit: 0,
      timezone: 'America/Toronto',
      default_report_recipient: 'himanshusoni3214@gmail.com',
      notes: `${runId} isolated company`,
      status: 'Active',
    });
    const companyDb = await dbRow<ApiRecord>('select name, website, industry, daily_email_limit, timezone, default_report_recipient, notes, status::text as status from companies where id = $1', [ids.company]);
    expect(companyDb).toMatchObject({
      name: companyName,
      website: 'https://qa-edited.example.invalid',
      industry: 'QA Automation Edited',
      daily_email_limit: 0,
      timezone: 'America/Toronto',
      default_report_recipient: 'himanshusoni3214@gmail.com',
      notes: `${runId} isolated company`,
      status: 'active',
    });
    record('Companies', 'Partial edit made one PUT and preserved untouched fields', 'PASS');

    await crudRow(page, companyName).getByRole('button', { name: 'Archive' }).click();
    await expect(crudRow(page, companyName)).toContainText('Archived');
    await crudRow(page, companyName).getByRole('button', { name: 'Restore' }).click();
    await expect(crudRow(page, companyName)).toContainText('Active');
    const companyAfterRestore = await apiGet<ApiRecord[]>('/companies').then((rows) => rows.find((row) => row.id === ids.company));
    expect(companyAfterRestore).toMatchObject({
      name: companyName,
      daily_email_limit: 0,
      timezone: 'America/Toronto',
      default_report_recipient: 'himanshusoni3214@gmail.com',
      notes: `${runId} isolated company`,
      status: 'Active',
    });
    record('Companies', 'Archive and restore through UI', 'PASS');

    const campaignName = `${runId} Campaign`;
    await page.goto('/campaigns', { waitUntil: 'domcontentloaded' });
    await selectCompany(page, ids.company!);
    await fillCrud(page, {
      name: campaignName,
      description: `${runId} campaign description`,
      industry: 'QA',
      target_audience: `${runId} target audience`,
      geographic_area: 'Toronto QA',
      daily_lead_goal: 1,
      daily_email_goal: 0,
      daily_email_limit: 0,
      dry_run_mode: true,
      timezone: 'America/Toronto',
      internal_test_recipient: 'himanshusoni3214@gmail.com',
      report_recipient: 'himanshusoni3214@gmail.com',
      status: 'Active',
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
    const campaignAfterEdit = await apiGet<ApiRecord[]>(`/campaigns?company_id=${ids.company}`).then((rows) => rows.find((row) => row.id === ids.campaign));
    expect(campaignAfterEdit).toMatchObject({
      name: campaignName,
      description: `${runId} campaign description`,
      target_audience: `${runId} target audience`,
      geographic_area: 'Toronto QA',
      daily_lead_goal: 1,
      daily_email_goal: 0,
      daily_email_limit: 0,
      timezone: 'America/Toronto',
      internal_test_recipient: 'himanshusoni3214@gmail.com',
      report_recipient: 'himanshusoni3214@gmail.com',
      dry_run_mode: true,
      status: 'Active',
    });
    await crudRow(page, campaignName).getByRole('button', { name: 'Pause' }).click();
    await expect(crudRow(page, campaignName)).toContainText('Inactive');
    await crudRow(page, campaignName).getByRole('button', { name: 'Resume' }).click();
    await expect(crudRow(page, campaignName)).toContainText('Active');
    await crudRow(page, campaignName).getByRole('button', { name: 'Duplicate' }).click();
    const duplicate = await findByName('/campaigns', `${campaignName} Copy`, `?company_id=${ids.company}`);
    ids.duplicateCampaign = duplicate.id;
    await crudRow(page, campaignName).getByRole('button', { name: 'Archive' }).click();
    await expect(crudRow(page, campaignName)).toContainText('Archived');
    await crudRow(page, campaignName).getByRole('button', { name: 'Restore' }).click();
    await expect(crudRow(page, campaignName)).toContainText('Active');
    const campaignAfterRestore = await apiGet<ApiRecord[]>(`/campaigns?company_id=${ids.company}`).then((rows) => rows.find((row) => row.id === ids.campaign));
    expect(campaignAfterRestore).toMatchObject({
      name: campaignName,
      daily_lead_goal: 1,
      daily_email_goal: 0,
      daily_email_limit: 0,
      timezone: 'America/Toronto',
      report_recipient: 'himanshusoni3214@gmail.com',
      status: 'Active',
    });
    record('Campaigns', 'Edit, pause, resume, duplicate, archive and restore through UI', 'PASS', duplicate.id);

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
    const scheduleRun = await apiPost<ApiRecord>(`/schedules/${ids.schedule}/run`);
    const scheduleDryRun = await apiPostRaw(`/schedules/${ids.schedule}/dry-run`);
    const scheduleTestRun = await apiPostRaw(`/schedules/${ids.schedule}/test-run`);
    expect(['blocked', 'skipped'].includes(String(scheduleRun.state).toLowerCase())).toBeTruthy();
    expect(scheduleDryRun.status).toBe(501);
    expect(scheduleTestRun.status).toBe(501);
    const scheduleJobs = await apiGet<ApiRecord[]>(`/jobs?employee_id=${ids.employee}`);
    expect(scheduleJobs.some((job) => ['Blocked', 'Skipped', 'Queued'].includes(job.status))).toBeTruthy();
    record('Schedules', 'Run Now blocked/skipped and Dry Run/Test Run return truthful unsupported response in jobs_json mode', 'PASS', `${scheduleJobs.length} jobs`);
    record('Schedules', 'Schedule cleanup behavior', 'PASS', 'API-only cleanup; UI intentionally has no Schedule archive/delete button');

    await page.goto(`/employees?company_id=${ids.company}&campaign_id=${ids.campaign}`, { waitUntil: 'domcontentloaded' });
    const employeeRun = await apiPost<ApiRecord>(`/employees/${ids.employee}/run`);
    const employeeDryRun = await apiPostRaw(`/employees/${ids.employee}/dry-run`);
    expect(['blocked', 'skipped'].includes(String(employeeRun.state).toLowerCase())).toBeTruthy();
    expect(employeeDryRun.status).toBe(501);
    const employeeJobs = await apiGet<ApiRecord[]>(`/jobs?employee_id=${ids.employee}`);
    expect(employeeJobs.some((job) => ['Blocked', 'Skipped', 'Queued'].includes(job.status))).toBeTruthy();
    record('Employees', 'Run remains blocked/skipped and Dry Run returns truthful unsupported response in jobs_json mode', 'PASS', `${employeeJobs.length} jobs`);

    const lead = await apiPost<ApiRecord>('/leads', {
      company_id: ids.company,
      campaign_id: ids.campaign,
      name: `${runId} Lead`,
      business: `${runId} Business`,
      email: leadEmail,
      status: 'Generated',
    });
    ids.lead = lead.id;
    await apiPut(`/leads/${ids.lead}`, { ...lead, status: 'Verified', phone: '000-000-0000' });
    expect(await dbCount('leads', ids.lead!)).toBe(1);
    record('Leads', 'Create and update via API/PostgreSQL using example.invalid', 'PASS', ids.lead);

    const report = await apiPost<ApiRecord>('/reports/daily', { company_id: ids.company, campaign_id: ids.campaign, send_email: false });
    expect(report.report_run.status).toBe('generated');
    ids.reportRun = report.report_run.id;
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
