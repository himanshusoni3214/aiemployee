import { serverApi } from '../../lib/serverApi';
import { ScheduleActions } from '../../components/ActionButtons';

type Employee = { id: string; name: string; status: string };
type Schedule = {
  id: string;
  employee_id: string;
  name: string;
  cron: string;
  task_type: string;
  payload?: Record<string, unknown>;
  is_paused: boolean;
  last_run_at?: string | null;
  next_run_at?: string | null;
};

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '-';
}

function hermesId(schedule: Schedule) {
  const value = schedule.payload?.hermes_job_id;
  return typeof value === 'string' ? value : '-';
}

export default async function SchedulerPage() {
  const [schedules, employees] = await Promise.all([
    serverApi<Schedule[]>('/schedules', []),
    serverApi<Employee[]>('/employees', []),
  ]);
  const employeeName = new Map(employees.map((employee) => [employee.id, employee.name]));
  const runningEmployees = new Set(employees.filter((employee) => employee.status === 'Running').map((employee) => employee.id));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Scheduler</h1>
        <div className="text-sm text-zinc-400">{schedules.length} schedules</div>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <div className="card"><p className="text-sm text-zinc-400">Active</p><p className="mt-2 text-3xl font-semibold">{schedules.filter((schedule) => !schedule.is_paused).length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Paused</p><p className="mt-2 text-3xl font-semibold">{schedules.filter((schedule) => schedule.is_paused).length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Running Workers</p><p className="mt-2 text-3xl font-semibold">{runningEmployees.size}</p></div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Schedule</th><th>Employee</th><th>Task</th><th>Cron</th><th>Status</th><th>Last Run</th><th>Next Run</th><th>Hermes ID</th><th>Actions</th></tr></thead>
          <tbody>
            {schedules.map((schedule) => (
              <tr key={schedule.id}>
                <td className="font-medium text-stone-100">{schedule.name}</td>
                <td>{employeeName.get(schedule.employee_id) || schedule.employee_id}</td>
                <td>{schedule.task_type}</td>
                <td>{schedule.cron}</td>
                <td>{schedule.is_paused ? 'Paused' : 'Active'}</td>
                <td>{formatDate(schedule.last_run_at)}</td>
                <td>{formatDate(schedule.next_run_at)}</td>
                <td className="text-zinc-400">{hermesId(schedule)}</td>
                <td><ScheduleActions id={schedule.id} isPaused={schedule.is_paused} /></td>
              </tr>
            ))}
            {!schedules.length ? <tr><td colSpan={9} className="text-zinc-400">No schedules imported from Hermes yet</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
