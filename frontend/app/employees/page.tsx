import { serverApi } from '../../lib/serverApi';

type Company = { id: string; name: string };
type Employee = {
  id: string;
  company_id: string;
  name: string;
  employee_type: string;
  status: string;
  rate_limit_per_hour?: number;
  daily_email_limit?: number;
  failure_count?: number;
  circuit_breaker_open?: boolean;
  paused_reason?: string | null;
  last_error?: string | null;
  last_heartbeat_at?: string | null;
};

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '-';
}

function reason(employee: Employee) {
  return employee.last_error || employee.paused_reason || '-';
}

export default async function EmployeesPage() {
  const [employees, companies] = await Promise.all([
    serverApi<Employee[]>('/employees', []),
    serverApi<Company[]>('/companies', []),
  ]);
  const companyName = new Map(companies.map((company) => [company.id, company.name]));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">AI Employees</h1>
        <div className="text-sm text-zinc-400">{employees.length} workers</div>
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        <div className="card"><p className="text-sm text-zinc-400">Running</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Running').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Paused</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Paused').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Errors</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Error').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Open Circuits</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.circuit_breaker_open).length}</p></div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Name</th><th>Company</th><th>Type</th><th>Status</th><th>Limits</th><th>Circuit</th><th>Last Heartbeat</th><th>Reason</th></tr></thead>
          <tbody>
            {employees.map((employee) => (
              <tr key={employee.id}>
                <td className="font-medium text-stone-100">{employee.name}</td>
                <td>{companyName.get(employee.company_id) || employee.company_id}</td>
                <td>{employee.employee_type}</td>
                <td>{employee.status}</td>
                <td>{employee.rate_limit_per_hour ?? 0}/hr, {employee.daily_email_limit ?? 0}/day</td>
                <td>{employee.circuit_breaker_open ? 'Open' : 'Closed'} ({employee.failure_count ?? 0})</td>
                <td>{formatDate(employee.last_heartbeat_at)}</td>
                <td className="max-w-sm truncate text-zinc-400">{reason(employee)}</td>
              </tr>
            ))}
            {!employees.length ? <tr><td colSpan={8} className="text-zinc-400">No workers imported from Hermes yet</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
