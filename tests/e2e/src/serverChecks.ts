import fs from 'node:fs';
import path from 'node:path';
import { Client } from 'pg';
import {
  BREW_COMPANY_ID,
  BREW_COMPANY_NAME,
  EXPECTED_COUNTS,
  apiUrl,
  auditDir,
  hermesDataPath,
  normalizeDatabaseUrl,
  requiredEnv,
} from './env';

type ApiRecord = Record<string, any>;

export type ServerCheckResult = {
  context: string;
  ok: boolean;
  checkedAt: string;
  api: {
    companyId: string;
    campaigns: number;
    employees: number;
    schedules: number;
    jobs: number;
    workersStatus?: ApiRecord;
    systemHealth?: ApiRecord;
  };
  database: {
    campaigns: number;
    employees: number;
    schedules: number;
  };
  hermes: {
    jobsFile: string;
    jobsCount: number;
    scheduleHermesIds: string[];
    missingScheduleHermesIds: string[];
    outreachFollowupPaused: boolean | null;
  };
};

function assertEqual(label: string, actual: number, expected: number) {
  if (actual !== expected) throw new Error(`${label} expected ${expected}, got ${actual}`);
}

function active(records: ApiRecord[]) {
  return records.filter((record) => String(record.status || '').toLowerCase() !== 'archived');
}

async function apiLogin() {
  const response = await fetch(`${apiUrl()}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      email: requiredEnv('VORYX_QA_ADMIN_EMAIL'),
      password: requiredEnv('VORYX_QA_ADMIN_PASSWORD'),
    }),
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`QA API login failed (${response.status}): ${body}`);
  const data = JSON.parse(body);
  if (!data.access_token) throw new Error('QA API login did not return an access token');
  return data.access_token as string;
}

async function apiGet<T>(token: string, pathName: string): Promise<T> {
  const response = await fetch(`${apiUrl()}/api${pathName}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`GET /api${pathName} failed (${response.status}): ${body}`);
  return JSON.parse(body) as T;
}

function loadHermesJobs() {
  const jobsFile = path.join(hermesDataPath(), 'cron', 'jobs.json');
  const raw = JSON.parse(fs.readFileSync(jobsFile, 'utf8'));
  const jobs = Array.isArray(raw) ? raw : Array.isArray(raw.jobs) ? raw.jobs : Array.isArray(raw.schedules) ? raw.schedules : [];
  return { jobsFile, jobs };
}

function isPausedOutreachFollowup(job: ApiRecord) {
  const name = `${job.name || ''} ${job.id || ''}`.toLowerCase();
  if (!name.includes('outreach') || !name.includes('follow')) return null;
  const state = String(job.state || job.status || '').toLowerCase();
  return job.enabled === false || ['paused', 'disabled', 'stopped'].includes(state) || Boolean(job.paused_at || job.paused_reason);
}

async function databaseCounts() {
  const client = new Client({ connectionString: normalizeDatabaseUrl() });
  await client.connect();
  try {
    const [campaigns, employees, schedules] = await Promise.all([
      client.query("select count(*)::int as count from campaigns where company_id = $1 and lower(status::text) <> 'archived'", [BREW_COMPANY_ID]),
      client.query("select count(*)::int as count from ai_employees where company_id = $1 and lower(status::text) <> 'archived'", [BREW_COMPANY_ID]),
      client.query(
        "select count(*)::int as count from schedules s join ai_employees e on e.id = s.employee_id where e.company_id = $1 and lower(e.status::text) <> 'archived'",
        [BREW_COMPANY_ID],
      ),
    ]);
    return {
      campaigns: campaigns.rows[0].count as number,
      employees: employees.rows[0].count as number,
      schedules: schedules.rows[0].count as number,
    };
  } finally {
    await client.end();
  }
}

export async function runServerChecks(context: string): Promise<ServerCheckResult> {
  const token = await apiLogin();
  const companies = await apiGet<ApiRecord[]>(token, '/companies');
  const company = companies.find((item) => item.id === BREW_COMPANY_ID);
  if (!company) throw new Error(`API did not return company ${BREW_COMPANY_ID}`);
  if (company.name !== BREW_COMPANY_NAME) throw new Error(`Company ${BREW_COMPANY_ID} expected ${BREW_COMPANY_NAME}, got ${company.name}`);

  const [campaignsRaw, employeesRaw, schedulesRaw, jobs, workersStatus, systemHealth] = await Promise.all([
    apiGet<ApiRecord[]>(token, `/campaigns?company_id=${BREW_COMPANY_ID}`),
    apiGet<ApiRecord[]>(token, `/employees?company_id=${BREW_COMPANY_ID}`),
    apiGet<ApiRecord[]>(token, `/schedules?company_id=${BREW_COMPANY_ID}`),
    apiGet<ApiRecord[]>(token, `/jobs?company_id=${BREW_COMPANY_ID}`),
    apiGet<ApiRecord>(token, `/workers/status?company_id=${BREW_COMPANY_ID}`),
    apiGet<ApiRecord>(token, `/system/health?company_id=${BREW_COMPANY_ID}`),
  ]);
  const campaigns = active(campaignsRaw);
  const employees = active(employeesRaw);
  const schedules = schedulesRaw.filter((schedule) => employees.some((employee) => employee.id === schedule.employee_id));

  assertEqual('API campaigns', campaigns.length, EXPECTED_COUNTS.campaigns);
  assertEqual('API employees', employees.length, EXPECTED_COUNTS.employees);
  assertEqual('API schedules', schedules.length, EXPECTED_COUNTS.schedules);

  const db = await databaseCounts();
  assertEqual('PostgreSQL campaigns', db.campaigns, EXPECTED_COUNTS.campaigns);
  assertEqual('PostgreSQL employees', db.employees, EXPECTED_COUNTS.employees);
  assertEqual('PostgreSQL schedules', db.schedules, EXPECTED_COUNTS.schedules);

  const { jobsFile, jobs: hermesJobs } = loadHermesJobs();
  if (!hermesJobs.length) throw new Error(`Hermes jobs.json has no jobs: ${jobsFile}`);
  const hermesIds = new Set(hermesJobs.map((job: ApiRecord) => String(job.id || '')).filter(Boolean));
  const scheduleHermesIds = schedules.map((schedule) => schedule.payload?.hermes_job_id).filter(Boolean).map(String);
  const missingScheduleHermesIds = scheduleHermesIds.filter((id) => !hermesIds.has(id));
  if (missingScheduleHermesIds.length) throw new Error(`Hermes jobs.json missing schedule IDs: ${missingScheduleHermesIds.join(', ')}`);

  const followupStates = hermesJobs.map(isPausedOutreachFollowup).filter((value: boolean | null): value is boolean => value !== null);
  const outreachFollowupPaused = followupStates.length ? followupStates.every(Boolean) : null;
  if (outreachFollowupPaused === false) throw new Error('Hermes Outreach Followup is not paused/disabled in jobs.json');

  const result: ServerCheckResult = {
    context,
    ok: true,
    checkedAt: new Date().toISOString(),
    api: {
      companyId: BREW_COMPANY_ID,
      campaigns: campaigns.length,
      employees: employees.length,
      schedules: schedules.length,
      jobs: jobs.length,
      workersStatus,
      systemHealth,
    },
    database: db,
    hermes: {
      jobsFile,
      jobsCount: hermesJobs.length,
      scheduleHermesIds,
      missingScheduleHermesIds,
      outreachFollowupPaused,
    },
  };
  fs.writeFileSync(path.join(auditDir(), `server-check-${context}.json`), JSON.stringify(result, null, 2));
  return result;
}
