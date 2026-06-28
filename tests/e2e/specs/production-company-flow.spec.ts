import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { BREW_COMPANY_ID, BREW_COMPANY_NAME, ROUTES, auditDir, requiredEnv, slug, type RouteConfig } from '../src/env';
import { writeQaReport, writeRouteResult, type RouteResult } from '../src/report';
import { runServerChecks } from '../src/serverChecks';

type Captures = {
  consoleErrors: string[];
  failedRequests: string[];
};

function attachCapture(page: Page): Captures {
  const captures: Captures = { consoleErrors: [], failedRequests: [] };
  page.on('console', (message) => {
    if (message.type() === 'error') captures.consoleErrors.push(message.text());
  });
  page.on('requestfailed', (request) => {
    captures.failedRequests.push(`${request.method()} ${request.url()} ${request.failure()?.errorText || ''}`.trim());
  });
  page.on('response', (response) => {
    const url = response.url();
    const status = response.status();
    if (status >= 400 && !url.endsWith('/favicon.ico')) captures.failedRequests.push(`${status} ${url}`);
  });
  return captures;
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

async function routeScreenshot(page: Page, route: RouteConfig, phase: string) {
  const file = path.join(auditDir(), 'screenshots', `${slug(route.path)}-${phase}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

async function dataRowCount(page: Page) {
  const texts = await page.locator('table.ops-table tbody tr').evaluateAll((rows) => rows.map((row) => row.textContent?.trim() || ''));
  return texts.filter((text) => text && !text.startsWith('No ') && !text.includes('Loading')).length;
}

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function assertBrewSelected(page: Page, route: RouteConfig) {
  await expect(page).toHaveURL(new RegExp(`${route.path.replace('/', '\\/')}\\?[^#]*company_id=${BREW_COMPANY_ID}`));
  await expect(page.getByRole('heading', { name: route.heading, exact: true })).toBeVisible();
  await expect(page.getByText(new RegExp(`Companies\\s*>\\s*${escapeRegex(BREW_COMPANY_NAME)}`))).toBeVisible();
  await expect(page.getByLabel('Select company', { exact: true })).toHaveValue(BREW_COMPANY_ID);
}

async function runRouteFlow(page: Page, route: RouteConfig): Promise<RouteResult> {
  const captures = attachCapture(page);
  const screenshots: string[] = [];
  let browserRows: number | null = null;
  let serverCheck: unknown = null;
  const resultBase = {
    route: route.path,
    checkedAt: new Date().toISOString(),
    screenshots,
    consoleErrors: captures.consoleErrors,
    failedRequests: captures.failedRequests,
  };

  try {
    await login(page);
    await page.goto(route.path, { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(new RegExp(`${route.path}(?!.*company_id)`));
    await expect(page.getByLabel('Select company', { exact: true })).toBeVisible();

    screenshots.push(await routeScreenshot(page, route, 'no-company'));
    await page.getByLabel('Select company', { exact: true }).selectOption({ label: BREW_COMPANY_NAME });
    await assertBrewSelected(page, route);

    serverCheck = await runServerChecks(slug(route.path));
    if ('expectedRows' in route && route.expectedRows) {
      browserRows = await dataRowCount(page);
      expect(browserRows, `${route.path} visible table rows`).toBe(route.expectedRows);
    }
    screenshots.push(await routeScreenshot(page, route, 'selected'));

    await page.reload({ waitUntil: 'domcontentloaded' });
    await assertBrewSelected(page, route);
    if ('expectedRows' in route && route.expectedRows) {
      browserRows = await dataRowCount(page);
      expect(browserRows, `${route.path} rows after hard refresh`).toBe(route.expectedRows);
    }
    screenshots.push(await routeScreenshot(page, route, 'hard-refresh'));

    await page.goBack({ waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(new RegExp(`${route.path}(?!.*company_id)`));
    await expect(page.getByLabel('Select company', { exact: true })).not.toHaveValue(BREW_COMPANY_ID);

    await page.goForward({ waitUntil: 'domcontentloaded' });
    await assertBrewSelected(page, route);
    if ('expectedRows' in route && route.expectedRows) {
      browserRows = await dataRowCount(page);
      expect(browserRows, `${route.path} rows after forward navigation`).toBe(route.expectedRows);
    }
    screenshots.push(await routeScreenshot(page, route, 'forward'));

    expect(captures.consoleErrors, `${route.path} browser console errors`).toEqual([]);
    expect(captures.failedRequests, `${route.path} failed network requests`).toEqual([]);

    return {
      ...resultBase,
      ok: true,
      finalUrl: page.url(),
      browserRows,
      serverCheck,
    };
  } catch (error) {
    screenshots.push(await routeScreenshot(page, route, 'failure').catch(() => ''));
    return {
      ...resultBase,
      ok: false,
      finalUrl: page.url(),
      browserRows,
      serverCheck,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

test('server-side production baseline matches Brew It By Sash state', async () => {
  await runServerChecks('baseline');
});

for (const route of ROUTES) {
  test(`${route.path} company dropdown persists Brew It By Sash selection`, async ({ page }) => {
    const result = await runRouteFlow(page, route);
    writeRouteResult(result);
    expect(result.ok, result.error || `${route.path} failed`).toBe(true);
  });
}

test.afterAll(async () => {
  writeQaReport();
});
