'use client';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';

const statuses = ['', 'Queued', 'Running', 'Completed', 'Failed'];

export default function JobsPage() {
  const [jobs, setJobs] = useState<any[]>([]);
  const [employees, setEmployees] = useState<any[]>([]);
  const [campaigns, setCampaigns] = useState<any[]>([]);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const [form, setForm] = useState({ employee_id: '', campaign_id: '', connector: 'hermes', task_type: 'Generate Leads', payload: {}, max_attempts: 1 });

  async function load(nextStatus = status) {
    try {
      const query = nextStatus ? `?status=${encodeURIComponent(nextStatus)}` : '';
      const [jobRows, employeeRows, campaignRows] = await Promise.all([api(`/jobs${query}`), api('/employees'), api('/campaigns')]);
      setJobs(jobRows);
      setEmployees(employeeRows);
      setCampaigns(campaignRows);
      setError('');
    } catch {
      location.href = '/login';
    }
  }

  useEffect(() => { load(); const id = setInterval(() => load(), 15000); return () => clearInterval(id); }, []);

  async function create() {
    try {
      await api('/jobs', { method: 'POST', body: JSON.stringify({ ...form, employee_id: form.employee_id || null, campaign_id: form.campaign_id || null }) });
      await load();
    } catch (err: any) {
      setError(err.message || 'Request failed');
    }
  }

  async function retry(id: string) {
    await api(`/jobs/${id}/retry`, { method: 'POST' });
    await load();
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <select className="input max-w-48" value={status} onChange={(event) => { setStatus(event.target.value); load(event.target.value); }}>
          {statuses.map((value) => <option value={value} key={value}>{value || 'All statuses'}</option>)}
        </select>
      </div>
      <div className="card">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          <label className="grid gap-1 text-sm text-zinc-300"><span>employee</span><select className="input" value={form.employee_id} onChange={(event) => setForm({ ...form, employee_id: event.target.value })}><option value="">None</option>{employees.map((employee) => <option value={employee.id} key={employee.id}>{employee.name}</option>)}</select></label>
          <label className="grid gap-1 text-sm text-zinc-300"><span>campaign</span><select className="input" value={form.campaign_id} onChange={(event) => setForm({ ...form, campaign_id: event.target.value })}><option value="">None</option>{campaigns.map((campaign) => <option value={campaign.id} key={campaign.id}>{campaign.name}</option>)}</select></label>
          <label className="grid gap-1 text-sm text-zinc-300"><span>task</span><input className="input" value={form.task_type} onChange={(event) => setForm({ ...form, task_type: event.target.value })} /></label>
          <label className="grid gap-1 text-sm text-zinc-300"><span>attempts</span><input className="input" type="number" min={1} max={3} value={form.max_attempts} onChange={(event) => setForm({ ...form, max_attempts: Number(event.target.value || 1) })} /></label>
          <div className="flex items-end"><button className="btn w-full" onClick={create}>Queue Job</button></div>
        </div>
        {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Task</th><th>Status</th><th>Attempts</th><th>Duration</th><th>Created</th><th>Error</th><th></th></tr></thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td className="font-medium text-stone-100">{job.task_type}</td>
                <td>{job.status}</td>
                <td>{job.attempts || 0}/{job.max_attempts || 1}</td>
                <td>{job.duration_seconds ? `${job.duration_seconds}s` : '-'}</td>
                <td>{new Date(job.created_at).toLocaleString()}</td>
                <td className="max-w-sm truncate text-zinc-400">{job.error_message || '-'}</td>
                <td>{job.status === 'Failed' ? <button className="btn-secondary" onClick={() => retry(job.id)}>Retry</button> : null}</td>
              </tr>
            ))}
            {!jobs.length ? <tr><td colSpan={7} className="text-zinc-400">No jobs</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
