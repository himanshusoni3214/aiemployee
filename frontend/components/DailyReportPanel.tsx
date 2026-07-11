'use client';

import { useState } from 'react';
import { api } from '../lib/api';

type Metric = { value: unknown; verified: boolean; source: string; note?: string };
type DailyReport = {
  report_date: string;
  generated_at: string;
  timezone: string;
  metrics: Record<string, Metric>;
  errors_and_blockers: string[];
  next_recommended_action: string;
};

export function DailyReportPanel({ initialReport, initialText }: { initialReport: DailyReport | null; initialText: string }) {
  const [date, setDate] = useState(initialReport?.report_date || new Date().toISOString().slice(0, 10));
  const [report, setReport] = useState<DailyReport | null>(initialReport);
  const [text, setText] = useState(initialText);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  async function generate(sendEmail: boolean) {
    setBusy(sendEmail ? 'send' : 'generate');
    setMessage('');
    setError('');
    try {
      const response = await api('/reports/daily', {
        method: 'POST',
        body: JSON.stringify({ report_date: date, send_email: sendEmail, recipient: 'himanshusoni3214@gmail.com' }),
      });
      setReport(response.report);
      setText(response.text);
      setMessage(sendEmail ? `Delivery ${response.delivery?.status || 'requested'}` : `Report written to ${response.report_run?.artifact_path}`);
      console.info('Daily report result', response);
    } catch (err: any) {
      console.error('Daily report failed', err);
      setError(err?.message || 'Report request failed');
    } finally {
      setBusy('');
    }
  }

  return (
    <div className="card space-y-4" data-voryx-daily-report>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">Deterministic Daily Report</h2>
          <p className="text-sm text-zinc-400">{report ? `Generated ${report.generated_at}` : 'No report loaded'}</p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="grid gap-1 text-sm text-zinc-300">
            <span>Toronto date</span>
            <input className="input" type="date" value={date} onChange={(event) => setDate(event.target.value)} />
          </label>
          <button className="btn-secondary" type="button" disabled={Boolean(busy)} onClick={() => generate(false)}>Generate</button>
          <button className="btn" type="button" disabled={Boolean(busy)} onClick={() => generate(true)}>Email Report</button>
        </div>
      </div>
      {message ? <p className="text-sm text-emerald-300">{message}</p> : null}
      {error ? <p className="text-sm text-red-300">{error}</p> : null}
      {report ? (
        <div className="grid gap-3 md:grid-cols-3">
          {Object.entries(report.metrics).map(([key, metric]) => (
            <div className="border border-zinc-800 p-3" key={key}>
              <p className="text-xs uppercase tracking-wide text-zinc-500">{key.replaceAll('_', ' ')}</p>
              <p className="mt-2 text-xl font-semibold">{String(metric.value)}</p>
              <p className={metric.verified ? 'text-xs text-emerald-300' : 'text-xs text-amber-300'}>{metric.verified ? 'Verified' : 'Unverified'}</p>
              <p className="mt-1 text-xs text-zinc-500">{metric.source}</p>
            </div>
          ))}
        </div>
      ) : null}
      <div>
        <h3 className="mb-2 text-sm font-medium text-zinc-300">Report Text</h3>
        <pre className="max-h-96 overflow-auto border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-300">{text || 'No report text'}</pre>
      </div>
    </div>
  );
}
