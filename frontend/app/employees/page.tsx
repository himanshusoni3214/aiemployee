import { serverApi } from '../../lib/serverApi';
import { EmployeeActions } from '../../components/ActionButtons';
import { LocalTime } from '../../components/LocalTime';
import { SyncStatus, type SyncInfo } from '../../components/SyncStatus';
import CrudPage from '../../components/CrudPage';
import { CompanySelector } from '../../components/CompanySelector';
import { QuerySelector } from '../../components/QuerySelector';
import { firstParam, queryString, selectedCompanyId } from '../../lib/companySelection';

type Company = { id: string; name: string; status: string };
type Campaign = { id: string; company_id: string; name: string; status: string };
type Employee = {
  id: string;
  company_id: string;
  campaign_id?: string | null;
  name: string;
  employee_type: string;
  hermes_job_id?: string | null;
  status: string;
  rate_limit_per_hour?: number;
  daily_email_limit?: number;
  failure_count?: number;
  circuit_breaker_open?: boolean;
  paused_reason?: string | null;
  last_error?: string | null;
  last_heartbeat_at?: string | null;
};

function reason(employee: Employee) {
  return employee.last_error || employee.paused_reason || '-';
}

export default async function EmployeesPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const companyQuery = queryString({ company_id: companyId || undefined });
  const campaigns = companyId ? await serverApi<Campaign[]>(`/campaigns${companyQuery}`, []) : [];
  const requestedCampaignId = firstParam(params.campaign_id);
  const campaignId = requestedCampaignId && campaigns.some((campaign) => campaign.id === requestedCampaignId) ? requestedCampaignId : '';
  const employeeQuery = queryString({ company_id: companyId || undefined, campaign_id: campaignId || undefined });
  const employees = companyId ? await serverApi<Employee[]>(`/employees${employeeQuery}`, []) : [];
  const sync = await serverApi<SyncInfo>('/sync/status', {});
  const companyName = new Map(companies.map((company) => [company.id, company.name]));
  const campaignName = new Map(campaigns.map((campaign) => [campaign.id, campaign.name]));
  const companyOptions = companies.filter((company) => company.status !== 'Archived').map((company) => ({ value: company.id, label: company.name }));
  const campaignOptions = campaigns.filter((campaign) => campaign.status !== 'Archived').map((campaign) => ({ value: campaign.id, label: campaign.name }));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-zinc-500">Companies &gt; {companyId ? companyName.get(companyId) : 'Select Company'} &gt; Employees</p>
          <h1 className="text-2xl font-semibold">AI Employees</h1>
        </div>
        <div className="flex items-center gap-4"><div className="text-sm text-zinc-400">{employees.length} workers</div><SyncStatus sync={sync} /></div>
      </div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} label="Company" />
      <div className="card flex flex-wrap items-end gap-3">
        <QuerySelector label="Campaign" param="campaign_id" value={campaignId} options={campaignOptions} allLabel="All campaigns" resetParams={['employee_id']} />
      </div>
      {!companyId ? <div className="card text-sm text-amber-300">Select a company to manage AI employees.</div> : null}
      <div className="grid gap-3 md:grid-cols-4">
        <div className="card"><p className="text-sm text-zinc-400">Running</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Running').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Paused</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Paused').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Errors</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Error').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Open Circuits</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.circuit_breaker_open).length}</p></div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Name</th><th>Company</th><th>Campaign</th><th>Type</th><th>Status</th><th>Limits</th><th>Circuit</th><th>Last Heartbeat</th><th>Reason</th><th>Actions</th></tr></thead>
          <tbody>
            {employees.map((employee) => (
              <tr key={employee.id}>
                <td className="font-medium text-stone-100">{employee.name}</td>
                <td>{companyName.get(employee.company_id) || employee.company_id}</td>
                <td>{employee.campaign_id ? campaignName.get(employee.campaign_id) || employee.campaign_id : '-'}</td>
                <td>{employee.employee_type}</td>
                <td>{employee.status}</td>
                <td>{employee.rate_limit_per_hour ?? 0}/hr, {employee.daily_email_limit ?? 0}/day</td>
                <td>{employee.circuit_breaker_open ? 'Open' : 'Closed'} ({employee.failure_count ?? 0})</td>
                <td><LocalTime value={employee.last_heartbeat_at} /></td>
                <td className="max-w-sm truncate text-zinc-400">{reason(employee)}</td>
                <td><EmployeeActions id={employee.id} status={employee.status} /></td>
              </tr>
            ))}
            {!employees.length ? <tr><td colSpan={10} className="text-zinc-400">{companyId ? 'No workers for selected filters' : 'No company selected'}</td></tr> : null}
          </tbody>
        </table>
      </div>
      {companyId ? (
        <CrudPage
          title="AI Employee Management"
          path="/employees"
          initialItems={employees}
          query={{ company_id: companyId, campaign_id: campaignId || undefined }}
          createLabel="Create AI Employee"
          emptyLabel="No workers for selected filters"
          displayMaps={{
            company_id: Object.fromEntries(companies.map((company) => [company.id, company.name])),
            campaign_id: Object.fromEntries(campaigns.map((campaign) => [campaign.id, campaign.name])),
          }}
          fields={{
            company_id: { type: 'select', label: 'Company', options: companyOptions },
            campaign_id: { type: 'select', label: 'Campaign', options: campaignOptions },
            employee_type: { type: 'select', label: 'Employee type', options: ['Lead Researcher', 'Email Outreach', 'Reply Handler', 'Appointment Setter', 'CRM Manager', 'Voice Agent', 'Custom'].map((value) => ({ value, label: value })) },
            hermes_job_id: { type: 'readonly', label: 'Hermes job ID', readOnly: true },
            approved_script: { type: 'readonly', label: 'Approved script', readOnly: true },
            working_directory: { type: 'readonly', label: 'Working directory', readOnly: true },
            prompt: { type: 'textarea' },
            daily_limits: { type: 'json' },
            dry_run_mode: { type: 'boolean', label: 'Dry-run mode' },
            status: { type: 'select', options: [{ value: 'Running', label: 'Running' }, { value: 'Paused', label: 'Paused' }, { value: 'Stopped', label: 'Stopped' }, { value: 'Error', label: 'Error' }, { value: 'Archived', label: 'Archived' }] },
          }}
          defaults={{
            company_id: companyId,
            campaign_id: campaignId || campaigns[0]?.id || '',
            name: '',
            employee_type: 'Custom',
            hermes_job_id: '',
            approved_script: '',
            working_directory: '/opt/data/home/leads',
            prompt: '',
            daily_limits: {},
            dry_run_mode: true,
            status: 'Stopped',
            rate_limit_per_hour: 20,
            daily_email_limit: 50,
          }}
        />
      ) : null}
    </div>
  );
}
