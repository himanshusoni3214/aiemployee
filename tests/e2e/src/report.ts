import fs from 'node:fs';
import path from 'node:path';
import { BREW_COMPANY_ID, BREW_COMPANY_NAME, EXPECTED_COUNTS, auditDir } from './env';

export type RouteResult = {
  route: string;
  ok: boolean;
  checkedAt: string;
  finalUrl?: string;
  browserRows?: number | null;
  screenshots: string[];
  consoleErrors: string[];
  failedRequests: string[];
  serverCheck?: unknown;
  error?: string;
};

function rel(file: string) {
  return path.relative(auditDir(), file);
}

export function writeRouteResult(result: RouteResult) {
  const file = path.join(auditDir(), 'route-results', `${result.route.replace(/^\//, '').replace(/[^a-z0-9]+/gi, '-')}.json`);
  fs.writeFileSync(file, JSON.stringify(result, null, 2));
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] || char));
}

function readJsonFiles(dir: string) {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir)
    .filter((file) => file.endsWith('.json'))
    .map((file) => JSON.parse(fs.readFileSync(path.join(dir, file), 'utf8')));
}

export function writeQaReport() {
  const dir = auditDir();
  const routeResults = readJsonFiles(path.join(dir, 'route-results')) as RouteResult[];
  const serverChecks = fs.readdirSync(dir)
    .filter((file) => file.startsWith('server-check-') && file.endsWith('.json'))
    .map((file) => JSON.parse(fs.readFileSync(path.join(dir, file), 'utf8')));
  const failures = routeResults.filter((result) => !result.ok);
  const networkFailures = routeResults.flatMap((result) => result.failedRequests.map((item) => `${result.route}: ${item}`));
  const consoleErrors = routeResults.flatMap((result) => result.consoleErrors.map((item) => `${result.route}: ${item}`));

  const lines = [
    '# Voryx Ops Production QA Report',
    '',
    `Generated: ${new Date().toISOString()}`,
    `Target company: ${BREW_COMPANY_NAME} (${BREW_COMPANY_ID})`,
    `Expected counts: campaigns=${EXPECTED_COUNTS.campaigns}, employees=${EXPECTED_COUNTS.employees}, schedules=${EXPECTED_COUNTS.schedules}`,
    `Overall status: ${failures.length || networkFailures.length || consoleErrors.length ? 'FAILED' : 'PASSED'}`,
    '',
    '## Route Results',
    '',
    '| Route | Status | Browser rows | Screenshots |',
    '| --- | --- | ---: | --- |',
    ...routeResults.map((result) => `| ${result.route} | ${result.ok ? 'PASS' : 'FAIL'} | ${result.browserRows ?? ''} | ${result.screenshots.map(rel).join('<br>')} |`),
    '',
    '## Server Cross-Checks',
    '',
    '```json',
    JSON.stringify(serverChecks, null, 2),
    '```',
    '',
    '## Browser Console Errors',
    '',
    consoleErrors.length ? consoleErrors.map((item) => `- ${item}`).join('\n') : 'None',
    '',
    '## Failed Network Requests',
    '',
    networkFailures.length ? networkFailures.map((item) => `- ${item}`).join('\n') : 'None',
    '',
    '## Failures',
    '',
    failures.length ? failures.map((result) => `- ${result.route}: ${result.error || 'Unknown failure'}`).join('\n') : 'None',
    '',
    '## Trace Artifacts',
    '',
    `Playwright output: ${path.join(dir, 'playwright-output')}`,
    `Playwright HTML report: ${path.join(dir, 'playwright-report', 'index.html')}`,
  ];

  const markdown = lines.join('\n');
  fs.writeFileSync(path.join(dir, 'QA_REPORT.md'), markdown);

  const html = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Voryx Ops Production QA Report</title>
  <style>
    body { font-family: Inter, Arial, sans-serif; margin: 32px; color: #111827; }
    table { border-collapse: collapse; width: 100%; margin: 16px 0; }
    th, td { border: 1px solid #d1d5db; padding: 8px; text-align: left; vertical-align: top; }
    th { background: #f3f4f6; }
    .pass { color: #047857; font-weight: 700; }
    .fail { color: #b91c1c; font-weight: 700; }
    pre { background: #f9fafb; border: 1px solid #e5e7eb; padding: 12px; overflow: auto; }
  </style>
</head>
<body>
  <h1>Voryx Ops Production QA Report</h1>
  <p><strong>Generated:</strong> ${escapeHtml(new Date().toISOString())}</p>
  <p><strong>Target company:</strong> ${escapeHtml(BREW_COMPANY_NAME)} (${escapeHtml(BREW_COMPANY_ID)})</p>
  <p><strong>Status:</strong> <span class="${failures.length || networkFailures.length || consoleErrors.length ? 'fail' : 'pass'}">${failures.length || networkFailures.length || consoleErrors.length ? 'FAILED' : 'PASSED'}</span></p>
  <h2>Route Results</h2>
  <table><thead><tr><th>Route</th><th>Status</th><th>Browser rows</th><th>Screenshots</th><th>Error</th></tr></thead><tbody>
    ${routeResults.map((result) => `<tr><td>${escapeHtml(result.route)}</td><td class="${result.ok ? 'pass' : 'fail'}">${result.ok ? 'PASS' : 'FAIL'}</td><td>${result.browserRows ?? ''}</td><td>${result.screenshots.map((item) => escapeHtml(rel(item))).join('<br>')}</td><td>${escapeHtml(result.error || '')}</td></tr>`).join('\n')}
  </tbody></table>
  <h2>Server Cross-Checks</h2>
  <pre>${escapeHtml(JSON.stringify(serverChecks, null, 2))}</pre>
  <h2>Browser Console Errors</h2>
  <pre>${escapeHtml(consoleErrors.join('\n') || 'None')}</pre>
  <h2>Failed Network Requests</h2>
  <pre>${escapeHtml(networkFailures.join('\n') || 'None')}</pre>
  <h2>Trace Artifacts</h2>
  <p>Playwright output: ${escapeHtml(path.join(dir, 'playwright-output'))}</p>
  <p>Playwright HTML report: ${escapeHtml(path.join(dir, 'playwright-report', 'index.html'))}</p>
</body>
</html>`;
  fs.writeFileSync(path.join(dir, 'QA_REPORT.html'), html);
}
