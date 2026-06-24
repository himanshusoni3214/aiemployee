'use client';
import { useEffect, useState } from 'react';
import { api } from '../../lib/api';

const employeeTypes = ['Lead Researcher', 'Email Outreach', 'Reply Handler', 'Appointment Setter', 'CRM Manager', 'Voice Agent'];

export default function EmployeesPage() {
  const [employees, setEmployees] = useState<any[]>([]);
  const [companies, setCompanies] = useState<any[]>([]);
  const [error, setError] = useState('');
  const [form, setForm] = useState({ company_id: '', name: '', employee_type: employeeTypes[0], prompt: '', daily_limits: {}, status: 'Stopped', rate_limit_per_hour: 20, daily_email_limit: 50 });

  async function load() {
    try {
      const [employeeRows, companyRows] = await Promise.all([api('/employees'), api('/companies')]);
      setEmployees(employeeRows);
      setCompanies(companyRows);
      if (!form.company_id && companyRows[0]) setForm((current) => ({ ...current, company_id: companyRows[0].id }));
      setError('');
    } catch {
      location.href = '/login';
    }
  }

  useEffect(() => { load(); }, []);

  async function create() {
    try {
      await api('/employees', { method: 'POST', body: JSON.stringify(form) });
      setForm({ ...form, name: '', prompt: '', status: 'Stopped' });
      await load();
    } catch (err: any) {
      setError(err.message || 'Request failed');
    }
  }

  async function action(id: string, name: string) {
    await api(`/employees/${id}/${name}`, { method: 'POST' });
    await load();
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">AI Employees</h1>
        <div className="text-sm text-zinc-400">{employees.length} workers</div>
      </div>
      <div className="card">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <label className="grid gap-1 text-sm text-zinc-300">
            <span>company</span>
            <select className="input" value={form.company_id} onChange={(event) => setForm({ ...form, company_id: event.target.value })}>
              <option value="">Select company</option>
              {companies.map((company) => <option value={company.id} key={company.id}>{company.name}</option>)}
            </select>
          </label>
          <label className="grid gap-1 text-sm text-zinc-300"><span>name</span><input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label className="grid gap-1 text-sm text-zinc-300">
            <span>type</span>
            <select className="input" value={form.employee_type} onChange={(event) => setForm({ ...form, employee_type: event.target.value })}>
              {employeeTypes.map((type) => <option key={type}>{type}</option>)}
            </select>
          </label>
          <label className="grid gap-1 text-sm text-zinc-300"><span>hourly limit</span><input className="input" type="number" value={form.rate_limit_per_hour} onChange={(event) => setForm({ ...form, rate_limit_per_hour: Number(event.target.value || 0) })} /></label>
          <label className="grid gap-1 text-sm text-zinc-300"><span>daily email limit</span><input className="input" type="number" value={form.daily_email_limit} onChange={(event) => setForm({ ...form, daily_email_limit: Number(event.target.value || 0) })} /></label>
          <label className="grid gap-1 text-sm text-zinc-300 md:col-span-2 xl:col-span-3"><span>prompt</span><textarea className="input min-h-24" value={form.prompt} onChange={(event) => setForm({ ...form, prompt: event.target.value })} /></label>
        </div>
        {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
        <button className="btn mt-4" onClick={create} disabled={!form.company_id || !form.name}>Create</button>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Name</th><th>Type</th><th>Status</th><th>Limits</th><th>Last heartbeat</th><th>Reason</th><th>Actions</th></tr></thead>
          <tbody>
            {employees.map((employee) => (
              <tr key={employee.id}>
                <td className="font-medium text-stone-100">{employee.name}</td>
                <td>{employee.employee_type}</td>
                <td>{employee.status}</td>
                <td>{employee.rate_limit_per_hour}/hr, {employee.daily_email_limit}/day</td>
                <td>{employee.last_heartbeat_at ? new Date(employee.last_heartbeat_at).toLocaleString() : '-'}</td>
                <td className="max-w-xs truncate text-zinc-400">{employee.paused_reason || employee.last_error || '-'}</td>
                <td><div className="flex flex-wrap gap-2"><button className="btn-secondary" onClick={() => action(employee.id, 'start')}>Start</button><button className="btn-secondary" onClick={() => action(employee.id, 'pause')}>Pause</button><button className="btn-secondary" onClick={() => action(employee.id, 'restart')}>Restart</button><button className="btn-danger" onClick={() => action(employee.id, 'stop')}>Stop</button></div></td>
              </tr>
            ))}
            {!employees.length ? <tr><td colSpan={7} className="text-zinc-400">No workers</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
