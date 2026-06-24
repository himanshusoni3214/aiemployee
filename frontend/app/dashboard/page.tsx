'use client';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';

const metricLabels: Record<string, string> = {
  todays_leads: "Today's Leads",
  verified_leads: 'Verified Leads',
  emails_sent: 'Emails Sent',
  replies: 'Replies',
  meetings: 'Meetings',
  failed_jobs: 'Failed Jobs',
};

function statusColor(status?: string) {
  if (status === 'Running' || status === 'ok') return '#2fbf71';
  if (status === 'Paused' || status === 'degraded') return '#d6a23a';
  if (status === 'Error' || status === 'Failed' || status === 'error') return '#d94b5b';
  return '#71717a';
}

export default function Dashboard() {
  const [report, setReport] = useState<any>();
  const [workers, setWorkers] = useState<any>();
  const [health, setHealth] = useState<any>();

  async function load() {
    try {
      const [reportData, workerData, healthData] = await Promise.all([api('/reports/ceo'), api('/workers/status'), api('/system/health')]);
      setReport(reportData);
      setWorkers(workerData);
      setHealth(healthData);
    } catch {
      location.href = '/login';
    }
  }

  useEffect(() => { load(); const id = setInterval(load, 20000); return () => clearInterval(id); }, []);
  const cards = Object.keys(metricLabels);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">CEO Dashboard</h1>
        <div className="text-sm text-zinc-400">System: <span style={{ color: statusColor(health?.status) }}>{health?.status || 'loading'}</span></div>
      </div>
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        {cards.map((key) => <div className="card" key={key}><p className="text-sm text-zinc-400">{metricLabels[key]}</p><p className="mt-2 text-3xl font-semibold">{report?.[key] ?? 0}</p></div>)}
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <section className="card">
          <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Worker Status</h2><span className="text-sm text-zinc-400">{workers?.employees?.length ?? 0} total</span></div>
          <div className="space-y-3">
            {workers?.employees?.slice(0, 8).map((worker: any) => (
              <div className="flex items-center justify-between gap-3 border-b border-zinc-800 pb-3 last:border-b-0 last:pb-0" key={worker.id}>
                <div><p className="font-medium">{worker.name}</p><p className="text-sm text-zinc-400">{worker.employee_type}</p></div>
                <div className="text-right"><p><span className="status-dot" style={{ background: statusColor(worker.status) }} />{worker.status}</p><p className="text-xs text-zinc-500">{worker.failure_count} failures</p></div>
              </div>
            ))}
            {!workers?.employees?.length ? <p className="text-sm text-zinc-400">No workers</p> : null}
          </div>
        </section>
        <section className="card">
          <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Job Queue</h2><span className="text-sm text-zinc-400">live</span></div>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(workers?.job_counts || {}).map(([key, value]) => <div className="border border-zinc-800 p-3" key={key}><p className="text-sm text-zinc-400">{key}</p><p className="text-2xl font-semibold">{String(value)}</p></div>)}
          </div>
          <div className="mt-4 border-t border-zinc-800 pt-4 text-sm text-zinc-400">
            Hermes: <span style={{ color: statusColor(health?.checks?.hermes?.status) }}>{health?.checks?.hermes?.status || 'unknown'}</span>
          </div>
        </section>
      </div>
    </div>
  );
}
