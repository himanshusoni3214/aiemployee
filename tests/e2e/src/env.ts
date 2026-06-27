import fs from 'node:fs';
import path from 'node:path';

export const BREW_COMPANY_ID = 'company-brew-it-by-sash';
export const BREW_COMPANY_NAME = 'Brew It By Sash';
export const EXPECTED_COUNTS = {
  campaigns: 3,
  employees: 4,
  schedules: 4,
};

export const ROUTES = [
  { path: '/campaigns', heading: 'Campaigns', rowKind: 'campaigns', expectedRows: EXPECTED_COUNTS.campaigns },
  { path: '/employees', heading: 'AI Employees', rowKind: 'employees', expectedRows: EXPECTED_COUNTS.employees },
  { path: '/scheduler', heading: 'Scheduler', rowKind: 'schedules', expectedRows: EXPECTED_COUNTS.schedules },
  { path: '/jobs', heading: 'Jobs' },
  { path: '/reports', heading: 'Activity Logs' },
  { path: '/system', heading: 'System Health' },
] as const;

export type RouteConfig = (typeof ROUTES)[number];

export function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

export function auditDir(): string {
  const dir = path.resolve(process.env.QA_AUDIT_DIR || path.join(process.cwd(), 'audits', `local-${Date.now()}`));
  fs.mkdirSync(dir, { recursive: true });
  fs.mkdirSync(path.join(dir, 'screenshots'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'route-results'), { recursive: true });
  return dir;
}

export function apiUrl(): string {
  return (process.env.API_URL || process.env.BASE_URL || 'https://ops.themealz.com').replace(/\/$/, '');
}

export function normalizeDatabaseUrl(): string {
  const raw = process.env.QA_DATABASE_URL || process.env.DATABASE_URL || '';
  if (!raw) throw new Error('Missing QA_DATABASE_URL or DATABASE_URL for PostgreSQL verification');
  return raw.replace(/^postgresql\+psycopg:\/\//, 'postgresql://');
}

export function hermesDataPath(): string {
  return requiredEnv('HERMES_DATA_PATH');
}

export function slug(value: string): string {
  return value.replace(/^\//, '').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '') || 'root';
}
