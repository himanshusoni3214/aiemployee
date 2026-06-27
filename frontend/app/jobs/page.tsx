import { serverApi } from '../../lib/serverApi';
import { LocalTime } from '../../components/LocalTime';
import { SyncStatus, type SyncInfo } from '../../components/SyncStatus';
import { CompanySelector } from '../../components/CompanySelector';
import { queryString, selectedCompanyId } from '../../lib/companySelection';

type Company = { id: string; name: string; status: string };
type Employee = { id: string; name: string };
type Campaign = { id: string; name: string };
type Job = {
  id: string;
  employee_id?: string | null;
  campaign_id?: string | null;
  connector: string;
  task_type: string;
  status: string;
  logs?: string[];
  error_message?: string | null;
  attempts?: number;
  max_attempts?: number;
  duration_seconds?: number | null;
  created_at?: string;
};

const statuses = ['Queued', 'Running', 'Completed', 'Failed', 'Blocked', 'Cancelled', 'Skipped'];

function lastLog(job: Job) {
  return job.error_message || job.logs?.[job.logs.length - 1] || '-';
}

export default async function JobsPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const scopedQuery = queryString({ company_id: companyId || undefined });
  const [jobs, employees, campaigns] = await Promise.all([
    serverApi<Job[]>(`/jobs${scopedQuery}`, []),
    serverApi<Employee[]>(`/employees${scopedQuery}`, []),
    serverApi<Campaign[]>(`/campaigns${scopedQuery}`, []),
  ]);
  const sync = await serverApi<SyncInfo>('/sync/status', {});
  const employeeName = new Map(employees.map((employee) => [employee.id, employee.name]));
  const campaignName = new Map(campaigns.map((campaign) => [campaign.id, campaign.name]));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Jobs</h1>
        <div className="flex items-center gap-4"><div className="text-sm text-zinc-400">{jobs.length} imported and queued jobs</div><SyncStatus sync={sync} /></div>
      </div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} allowAll label="Jobs scope" />
      <div className="grid gap-3 md:grid-cols-4 xl:grid-cols-7">
        {statuses.map((status) => (
          <div className="card" key={status}>
            <p className="text-sm text-zinc-400">{status}</p>
            <p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.status === status).length}</p>
          </div>
        ))}
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Task</th><th>Status</th><th>Employee</th><th>Campaign</th><th>Connector</th><th>Attempts</th><th>Created</th><th>Log</th></tr></thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td className="font-medium text-stone-100">{job.task_type}</td>
                <td>{job.status}</td>
                <td>{job.employee_id ? employeeName.get(job.employee_id) || job.employee_id : '-'}</td>
                <td>{job.campaign_id ? campaignName.get(job.campaign_id) || job.campaign_id : '-'}</td>
                <td>{job.connector}</td>
                <td>{job.attempts || 0}/{job.max_attempts || 1}</td>
                <td><LocalTime value={job.created_at} /></td>
                <td className="max-w-md truncate text-zinc-400">{lastLog(job)}</td>
              </tr>
            ))}
            {!jobs.length ? <tr><td colSpan={8} className="text-zinc-400">No jobs imported from Hermes yet</td></tr> : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}
