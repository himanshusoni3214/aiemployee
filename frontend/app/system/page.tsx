import { serverApi } from '../../lib/serverApi';

function color(status?: string) {
  if (status === 'ok') return 'text-emerald-300';
  if (status === 'degraded' || status === 'unknown' || status === 'unreachable') return 'text-amber-300';
  return 'text-red-300';
}

export default async function SystemPage() {
  const [health, workers, hermesLive] = await Promise.all([
    serverApi<any>('/system/health', { status: 'unknown', checks: {} }),
    serverApi<any>('/workers/status', { employees: [] }),
    serverApi<any>('/hermes/live', { status: 'unknown', jobs: [], outreach: {}, outputs: {} }),
  ]);
  const checks = Object.entries(health?.checks || {});

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">System Health</h1>
        <div className={`text-sm ${color(health?.status)}`}>{health?.status || 'unknown'}</div>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {checks.map(([name, check]: [string, any]) => <div className="card" key={name}><p className="text-sm capitalize text-zinc-400">{name}</p><p className={`mt-2 text-xl font-semibold ${color(check?.status)}`}>{check?.status || 'unknown'}</p><pre className="mt-3 max-h-36 overflow-auto text-xs text-zinc-500">{JSON.stringify(check, null, 2)}</pre></div>)}
      </div>
      <section className="card">
        <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Worker Circuit Breakers</h2><span className="text-sm text-zinc-400">{workers?.employees?.length ?? 0} workers</span></div>
        <div className="table-wrap border-0">
          <table className="ops-table">
            <thead><tr><th>Worker</th><th>Status</th><th>Circuit</th><th>Failures</th><th>Last Error</th></tr></thead>
            <tbody>
              {workers?.employees?.map((worker: any) => <tr key={worker.id}><td>{worker.name}</td><td>{worker.status}</td><td>{worker.circuit_breaker_open ? 'Open' : 'Closed'}</td><td>{worker.failure_count}</td><td className="max-w-sm truncate text-zinc-400">{worker.last_error || worker.paused_reason || '-'}</td></tr>)}
              {!workers?.employees?.length ? <tr><td colSpan={5} className="text-zinc-400">No workers</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold">Hermes Live Schedules</h2>
            <p className="text-sm text-zinc-400">{hermesLive?.data_path || hermesLive?.reason || 'Waiting for Hermes data mount'}</p>
          </div>
          <div className={`text-sm ${color(hermesLive?.status)}`}>{hermesLive?.status || 'unknown'}</div>
        </div>
        <div className="grid gap-3 md:grid-cols-4">
          <div className="border border-zinc-800 p-3"><p className="text-sm text-zinc-400">Jobs</p><p className="text-2xl font-semibold">{hermesLive?.job_count ?? 0}</p></div>
          <div className="border border-zinc-800 p-3"><p className="text-sm text-zinc-400">Enabled</p><p className="text-2xl font-semibold">{hermesLive?.enabled_job_count ?? 0}</p></div>
          <div className="border border-zinc-800 p-3"><p className="text-sm text-zinc-400">Failing</p><p className="text-2xl font-semibold">{hermesLive?.failing_job_count ?? 0}</p></div>
          <div className="border border-zinc-800 p-3"><p className="text-sm text-zinc-400">Key Limits</p><p className="text-2xl font-semibold">{hermesLive?.key_limit_failure_count ?? 0}</p></div>
        </div>
        <div className="mt-4 table-wrap border-0">
          <table className="ops-table">
            <thead><tr><th>Name</th><th>Schedule</th><th>State</th><th>Last Run</th><th>Last Status</th><th>Error</th></tr></thead>
            <tbody>
              {hermesLive?.jobs?.map((job: any) => <tr key={job.id}><td>{job.name}</td><td>{job.schedule_display || '-'}</td><td>{job.enabled ? job.state : 'disabled'}</td><td>{job.last_run_at || '-'}</td><td>{job.last_status || '-'}</td><td className="max-w-md truncate text-zinc-400">{job.last_error || job.last_delivery_error || '-'}</td></tr>)}
              {!hermesLive?.jobs?.length ? <tr><td colSpan={6} className="text-zinc-400">No mounted Hermes schedules</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>
      <section className="grid gap-5 xl:grid-cols-2">
        <div className="card">
          <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Outreach Log</h2><span className="text-sm text-zinc-400">{hermesLive?.outreach?.row_count ?? 0} rows</span></div>
          <div className="table-wrap border-0">
            <table className="ops-table">
              <thead><tr><th>Status</th><th>Timestamp</th><th>Note</th></tr></thead>
              <tbody>
                {hermesLive?.outreach?.recent?.map((row: any, index: number) => <tr key={`${row.timestamp}-${index}`}><td>{row.status || '-'}</td><td>{row.timestamp || '-'}</td><td className="max-w-sm truncate text-zinc-400">{row.note || '-'}</td></tr>)}
                {!hermesLive?.outreach?.recent?.length ? <tr><td colSpan={3} className="text-zinc-400">No outreach rows</td></tr> : null}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Hermes Outputs</h2><span className="text-sm text-zinc-400">{hermesLive?.outputs?.output_count ?? 0} files</span></div>
          <div className="space-y-2">
            {hermesLive?.outputs?.recent?.map((file: any) => <div className="border-b border-zinc-800 pb-2 last:border-b-0 last:pb-0" key={file.path}><p className="text-sm">{file.path}</p><p className="text-xs text-zinc-500">{file.size_bytes} bytes</p></div>)}
            {!hermesLive?.outputs?.recent?.length ? <p className="text-sm text-zinc-400">No output files mounted</p> : null}
          </div>
        </div>
      </section>
    </div>
  );
}
