import { serverApi } from '../../lib/serverApi';
import { ScheduleActions } from '../../components/ActionButtons';
import { LocalTime } from '../../components/LocalTime';
import { SyncStatus, type SyncInfo } from '../../components/SyncStatus';
import { CompanySelector } from '../../components/CompanySelector';
import { QuerySelector } from '../../components/QuerySelector';
import CrudPage from '../../components/CrudPage';
import { firstParam, queryString, selectedCompanyId } from '../../lib/companySelection';

type Company = { id: string; name: string; status: string };
type Campaign = { id: string; company_id: string; name: string; status: string };
type Employee = { id: string; company_id: string; campaign_id?: string | null; name: string; status: string };
type Schedule = {
  id: string;
  employee_id: string;
  name: string;
  cron: string;
  timezone: string;
  task_type: string;
  payload?: Record<string, unknown>;
  is_paused: boolean;
  last_run_at?: string | null;
  next_run_at?: string | null;
};

function hermesId(schedule: Schedule) {
  const value = schedule.payload?.hermes_job_id;
  return typeof value === 'string' ? value : '-';
}

export default async function SchedulerPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const companyQuery = queryString({ company_id: companyId || undefined });
  const campaigns = companyId ? await serverApi<Campaign[]>(`/campaigns${companyQuery}`, []) : [];
  const requestedCampaignId = firstParam(params.campaign_id);
  const campaignId = requestedCampaignId && campaigns.some((campaign) => campaign.id === requestedCampaignId) ? requestedCampaignId : '';
  const employees = companyId ? await serverApi<Employee[]>(`/employees${queryString({ company_id: companyId, campaign_id: campaignId || undefined })}`, []) : [];
  const requestedEmployeeId = firstParam(params.employee_id);
  const employeeId = requestedEmployeeId && employees.some((employee) => employee.id === requestedEmployeeId) ? requestedEmployeeId : '';
  const schedules = companyId ? await serverApi<Schedule[]>(`/schedules${queryString({ company_id: companyId, campaign_id: campaignId || undefined, employee_id: employeeId || undefined })}`, []) : [];
  const sync = await serverApi<SyncInfo>('/sync/status', {});
  const companyName = new Map(companies.map((company) => [company.id, company.name]));
  const employeeName = new Map(employees.map((employee) => [employee.id, employee.name]));
  const runningEmployees = new Set(employees.filter((employee) => employee.status === 'Running').map((employee) => employee.id));
  const campaignOptions = campaigns.filter((campaign) => campaign.status !== 'Archived').map((campaign) => ({ value: campaign.id, label: campaign.name }));
  const employeeOptions = employees.map((employee) => ({ value: employee.id, label: employee.name }));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-zinc-500">Companies &gt; {companyId ? companyName.get(companyId) : 'Select Company'} &gt; Scheduler</p>
          <h1 className="text-2xl font-semibold">Scheduler</h1>
        </div>
        <div className="flex items-center gap-4"><div className="text-sm text-zinc-400">{schedules.length} schedules</div><SyncStatus sync={sync} /></div>
      </div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} label="Company" />
      <div className="card flex flex-wrap items-end gap-3">
        <QuerySelector label="Campaign" param="campaign_id" value={campaignId} options={campaignOptions} allLabel="All campaigns" resetParams={['employee_id']} />
        <QuerySelector label="Employee" param="employee_id" value={employeeId} options={employeeOptions} allLabel="All employees" />
      </div>
      {!companyId ? <div className="card text-sm text-amber-300">Select a company to manage schedules.</div> : null}
      <div className="grid gap-3 md:grid-cols-3">
        <div className="card"><p className="text-sm text-zinc-400">Active</p><p className="mt-2 text-3xl font-semibold">{schedules.filter((schedule) => !schedule.is_paused).length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Paused</p><p className="mt-2 text-3xl font-semibold">{schedules.filter((schedule) => schedule.is_paused).length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Running Workers</p><p className="mt-2 text-3xl font-semibold">{runningEmployees.size}</p></div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Schedule</th><th>Employee</th><th>Task</th><th>Cron</th><th>Timezone</th><th>Status</th><th>Last Run</th><th>Next Run</th><th>Hermes ID</th><th>Actions</th></tr></thead>
          <tbody>
            {schedules.map((schedule) => (
              <tr key={schedule.id}>
                <td className="font-medium text-stone-100">{schedule.name}</td>
                <td>{employeeName.get(schedule.employee_id) || schedule.employee_id}</td>
                <td>{schedule.task_type}</td>
                <td>{schedule.cron}</td>
                <td>{schedule.timezone || 'America/Toronto'}</td>
                <td>{schedule.is_paused ? 'Paused' : 'Active'}</td>
                <td><LocalTime value={schedule.last_run_at} /></td>
                <td><LocalTime value={schedule.next_run_at} /></td>
                <td className="text-zinc-400">{hermesId(schedule)}</td>
                <td><ScheduleActions id={schedule.id} isPaused={schedule.is_paused} hermesJobId={hermesId(schedule) === '-' ? null : hermesId(schedule)} /></td>
              </tr>
            ))}
            {!schedules.length ? <tr><td colSpan={10} className="text-zinc-400">{companyId ? 'No schedules for selected filters' : 'No company selected'}</td></tr> : null}
          </tbody>
        </table>
      </div>
      {companyId ? (
        <CrudPage
          title="Schedule Management"
          path="/schedules"
          initialItems={schedules}
          query={{ company_id: companyId, campaign_id: campaignId || undefined, employee_id: employeeId || undefined }}
          createLabel="Create Schedule"
          emptyLabel="No schedules for selected filters"
          displayMaps={{ employee_id: Object.fromEntries(employees.map((employee) => [employee.id, employee.name])) }}
          fields={{
            employee_id: { type: 'select', label: 'Employee', options: employeeOptions },
            payload: { type: 'json' },
            is_paused: { type: 'boolean', label: 'Paused' },
          }}
          defaults={{
            employee_id: employeeId || employees[0]?.id || '',
            name: '',
            cron: '0 13 * * *',
            timezone: 'America/Toronto',
            task_type: 'Generate Leads',
            payload: {},
            is_paused: false,
          }}
        />
      ) : null}
    </div>
  );
}
