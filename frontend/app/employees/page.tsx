import { serverApi } from '../../lib/serverApi';
import { EmployeeActions, ScheduleActions, defaultConnectorCapabilities, type ConnectorCapabilities } from '../../components/ActionButtons';
import { isSafetyLockedHermesJob } from '../../lib/hermesSafety';
import { LocalTime } from '../../components/LocalTime';
import { SyncStatus, type SyncInfo } from '../../components/SyncStatus';
import CrudPage from '../../components/CrudPage';
import { CompanySelector } from '../../components/CompanySelector';
import { QuerySelector } from '../../components/QuerySelector';
import { firstParam, queryString, selectedCompanyId } from '../../lib/companySelection';

type CapabilitiesResponse = { hermes?: ConnectorCapabilities };
type Company = { id: string; name: string; status: string };
type Campaign = { id: string; company_id: string; name: string; status: string; campaign_type?: string; industry?: string; geographic_area?: string; target_audience?: string; description?: string; daily_lead_goal?: number; daily_email_goal?: number; daily_email_limit?: number; dry_run_mode?: boolean; report_recipient?: string; internal_test_recipient?: string; timezone?: string };
type Schedule = { id: string; employee_id: string; name: string; cron: string; timezone: string; task_type: string; payload?: Record<string, unknown>; is_paused: boolean; last_run_at?: string | null; next_run_at?: string | null };

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


const OPERATIONAL_TYPES = new Set(['Lead Researcher', 'CRM Manager', 'Report Manager', 'Daily Reporter', 'Email Outreach', 'Draft Writer', 'Outreach Draft Writer', 'Email Sender', 'Reply Monitor', 'Follow-up Manager', 'Followup Manager', 'Voice Agent', 'Custom']);
const BIBS_REAL_JOB_IDS = new Set(['0d0c20e25f55', '5881b72113ce', '47caae0a6a59', 'b03a2d0f1149']);

function isQaArtifact(employee: Employee) {
  const text = `${employee.id} ${employee.name} ${employee.hermes_job_id || ''}`.toLowerCase();
  return text.includes('model-policy-qa') || text.includes('template-sample') || text.includes('no-source') || text.includes('template-qa') || text.includes('generic-lead-research-qa') || text.includes('real-lead-qa');
}

function isOperationalWorker(employee: Employee) {
  if (employee.status === 'Archived') return false;
  if (!OPERATIONAL_TYPES.has(employee.employee_type)) return false;
  if (employee.hermes_job_id && BIBS_REAL_JOB_IDS.has(employee.hermes_job_id)) return true;
  if (isQaArtifact(employee)) return false;
  if (employee.employee_type === 'Custom') return Boolean(employee.hermes_job_id);
  return Boolean(employee.hermes_job_id);
}

function hermesId(schedule?: Schedule) {
  const value = schedule?.payload?.hermes_job_id;
  return typeof value === 'string' ? value : null;
}

function reason(employee: Employee) {
  return employee.last_error || employee.paused_reason || '-';
}

function statusLabel(employee: Employee) {
  return isSafetyLockedHermesJob(employee.hermes_job_id) ? 'Safety Locked' : employee.status;
}

function manualRunUnavailable(capabilities: ConnectorCapabilities, employee: Employee) {
  return employee.status === 'Scheduled' && !isSafetyLockedHermesJob(employee.hermes_job_id) && !capabilities.supports_manual_run && !capabilities.supports_dry_run;
}

function campaignReadyForLeadResearch(campaign?: Campaign) {
  return Boolean(campaign?.industry && campaign?.geographic_area && campaign?.target_audience && Number(campaign?.daily_lead_goal || 0) > 0 && campaign?.dry_run_mode !== false && !campaign?.daily_email_goal && !campaign?.daily_email_limit);
}
function campaignReadyForDailyReporter(campaign?: Campaign) {
  return Boolean((campaign?.report_recipient || campaign?.internal_test_recipient) && campaign?.timezone);
}
function campaignReadyForOutreachDraft(campaign?: Campaign) {
  return Boolean(campaign?.target_audience && campaign?.description && campaign?.dry_run_mode !== false && !campaign?.daily_email_goal && !campaign?.daily_email_limit);
}
function allowedEmployeeTypes(campaign?: Campaign) {
  const legacy = campaign?.campaign_type || '';
  if (legacy === 'lead_research') return ['Lead Researcher'];
  if (legacy === 'daily_reporting') return ['CRM Manager', 'Report Manager', 'Daily Reporter'];
  if (legacy === 'outreach_drafting') return ['Email Outreach', 'Draft Writer', 'Outreach Draft Writer'];
  const values: string[] = [];
  if (campaignReadyForLeadResearch(campaign)) values.push('Lead Researcher');
  if (campaignReadyForDailyReporter(campaign)) values.push('CRM Manager', 'Report Manager', 'Daily Reporter');
  if (campaignReadyForOutreachDraft(campaign)) values.push('Email Outreach', 'Draft Writer', 'Outreach Draft Writer');
  values.push('Custom');
  return Array.from(new Set(values));
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
  const allEmployees = companyId ? await serverApi<Employee[]>(`/employees${employeeQuery}`, []) : [];
  const schedules = companyId ? await serverApi<Schedule[]>(`/schedules${employeeQuery}`, []) : [];
  const employees = allEmployees.filter(isOperationalWorker);
  const [sync, capabilitiesResponse] = await Promise.all([
    serverApi<SyncInfo>('/sync/status', {}),
    serverApi<CapabilitiesResponse>('/connectors/capabilities', {}),
  ]);
  const capabilities = capabilitiesResponse.hermes || defaultConnectorCapabilities;
  const companyName = new Map(companies.map((company) => [company.id, company.name]));
  const campaignName = new Map(campaigns.map((campaign) => [campaign.id, campaign.name]));
  const scheduleByEmployee = new Map(schedules.map((schedule) => [schedule.employee_id, schedule]));
  const selectedCampaign = campaigns.find((campaign) => campaign.id === campaignId) || undefined;
  const employeeTypes = allowedEmployeeTypes(selectedCampaign);
  const employeeTypeOptions = employeeTypes.map((value) => ({ value, label: value }));
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
      {companyId && selectedCampaign ? (
        <div className="card grid gap-2 text-sm text-zinc-300" data-voryx-employee-template-readiness>
          <div>Available employee templates for {selectedCampaign.name}: {employeeTypes.join(', ')}</div>
          <div className="text-xs text-zinc-500">Lead Researcher: {campaignReadyForLeadResearch(selectedCampaign) ? 'available' : 'requires industry, city/region, target customer, lead goal and email disabled'}</div>
          <div className="text-xs text-zinc-500">Daily Reporter / CRM Manager: {campaignReadyForDailyReporter(selectedCampaign) ? 'available' : 'requires report recipient and timezone'}</div>
          <div className="text-xs text-zinc-500">Outreach Draft Writer: {campaignReadyForOutreachDraft(selectedCampaign) ? 'available' : 'requires offer/product, target customer, tone and no send action'}</div>
          <div className="text-xs text-zinc-500">Reply Handler: disabled, not connected. Voice Agent: disabled, not connected.</div>
        </div>
      ) : null}
      <div className="grid gap-3 md:grid-cols-5">
        <div className="card"><p className="text-sm text-zinc-400">Running</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Running').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Scheduled</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Scheduled').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Paused</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Paused').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Errors</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status === 'Error').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Open Circuits</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.circuit_breaker_open).length}</p></div>
      </div>
      {employees.length ? (
        <div className="grid gap-3 xl:grid-cols-2" data-voryx-employee-schedule-cards>
          {employees.map((employee) => {
            const schedule = scheduleByEmployee.get(employee.id);
            return (
              <section className="card" key={employee.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold">{employee.name}</h2>
                    <p className="text-xs text-zinc-500">{employee.employee_type} / {statusLabel(employee)} / Hermes {employee.hermes_job_id || 'not provisioned'}</p>
                    <p className="mt-1 text-xs text-zinc-500">Model Policy: effective policy is managed in System and Company Model Policy; employee override is available from employee detail API.</p>
                  </div>
                  <EmployeeActions id={employee.id} status={employee.status} hermesJobId={employee.hermes_job_id} capabilities={capabilities} showUnavailableMessage={false} />
                </div>
                <div className="mt-3 grid gap-2 text-xs text-zinc-400 md:grid-cols-2">
                  <div>Schedule: <span className="text-zinc-200">{schedule ? (schedule.is_paused ? 'Paused' : 'Active') : 'No schedule'}</span></div>
                  <div>Cron: <span className="text-zinc-200">{schedule?.cron || '-'}</span></div>
                  <div>Timezone: <span className="text-zinc-200">{schedule?.timezone || 'America/Toronto'}</span></div>
                  <div>Last run: <LocalTime value={schedule?.last_run_at} /></div>
                  <div>Next run: <LocalTime value={schedule?.next_run_at} /></div>
                  <div>Hermes synced: <span className="text-zinc-200">{schedule?.payload?.hermes_state ? 'Yes' : 'Not verified'}</span></div>
                </div>
                {schedule ? <div className="mt-3"><ScheduleActions id={schedule.id} isPaused={schedule.is_paused} hermesJobId={hermesId(schedule)} capabilities={capabilities} showUnavailableMessage={false} /></div> : null}
              </section>
            );
          })}
        </div>
      ) : null}
      <section className="card" data-voryx-disabled-worker-types>
        <h2 className="text-sm font-semibold">Not Connected Worker Types</h2>
        <p className="mt-2 text-xs text-zinc-400">Email Sender is blocked until prospect sending is enabled. Reply Monitor is disabled until Gmail thread monitoring is connected. Follow-up Manager stays disabled until Reply Monitor is connected and tested. Voice Agent is not connected.</p>
      </section>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Name</th><th>Company</th><th>Campaign</th><th>Type</th><th>Status</th><th>Limits</th><th>Circuit</th><th>Last Heartbeat</th><th>Reason</th><th>Actions</th></tr></thead>
          <tbody>
            {employees.map((employee) => (
              <tr key={employee.id}>
                <td className="font-medium text-stone-100">{employee.name}</td>
                <td>{companyName.get(employee.company_id) || employee.company_id}</td>
                <td>{employee.campaign_id ? campaignName.get(employee.campaign_id) || employee.campaign_id : '-'}</td>
                <td>
                  <div>{employee.employee_type}</div>
                  {employee.campaign_id && !allowedEmployeeTypes(campaigns.find((campaign) => campaign.id === employee.campaign_id)).includes(employee.employee_type)
                    ? <div className="text-xs text-amber-300">Not allowed for template</div>
                    : null}
                </td>
                <td>{statusLabel(employee)}</td>
                <td>{employee.rate_limit_per_hour ?? 0}/hr, {employee.daily_email_limit ?? 0}/day</td>
                <td>{employee.circuit_breaker_open ? 'Open' : 'Closed'} ({employee.failure_count ?? 0})</td>
                <td><LocalTime value={employee.last_heartbeat_at} /></td>
                <td className="max-w-sm truncate text-zinc-400">{reason(employee)}</td>
                <td><EmployeeActions id={employee.id} status={employee.status} hermesJobId={employee.hermes_job_id} capabilities={capabilities} showUnavailableMessage={false} />{manualRunUnavailable(capabilities, employee) ? <div className="mt-2 max-w-48 text-xs text-zinc-400" data-voryx-manual-run-unavailable>{capabilities.manual_run_message || 'Manual run unavailable in jobs_json mode'}</div> : null}</td>
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
            employee_type: { type: 'select', label: 'Employee template *', options: employeeTypeOptions },
            hermes_job_id: { type: 'readonly', label: 'Hermes job ID', readOnly: true },
            approved_script: { type: 'readonly', label: 'Approved script', readOnly: true },
            working_directory: { type: 'readonly', label: 'Working directory', readOnly: true },
            prompt: { type: 'textarea' },
            daily_limits: { type: 'json' },
            dry_run_mode: { type: 'boolean', label: 'Dry-run mode' },
            status: { type: 'select', options: [{ value: 'Running', label: 'Running' }, { value: 'Scheduled', label: 'Scheduled' }, { value: 'Paused', label: 'Paused' }, { value: 'Stopped', label: 'Stopped' }, { value: 'Error', label: 'Error' }, { value: 'Archived', label: 'Archived' }] },
          }}
          capabilities={capabilities}
          defaults={{
            company_id: companyId,
            campaign_id: campaignId || '',
            name: '',
            employee_type: employeeTypes[0] || 'Custom',
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
