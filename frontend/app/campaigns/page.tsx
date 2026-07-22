import { serverApi } from '../../lib/serverApi';
import CrudPage from '../../components/CrudPage';
import { defaultConnectorCapabilities, type ConnectorCapabilities } from '../../components/ActionButtons';
import { CompanySelector } from '../../components/CompanySelector';
import { LeadOutputsPanel } from '../../components/LeadOutputsPanel';
import { OutreachControlsPanel } from '../../components/OutreachControlsPanel';
import { DailyReportPanel } from '../../components/DailyReportPanel';
import { LeadSchemaEditor } from '../../components/LeadSchemaEditor';
import { queryString, selectedCompanyId } from '../../lib/companySelection';
import { ModelPolicyPanel } from '../../components/ModelPolicyPanel';
import { LocalTime } from '../../components/LocalTime';
import { SalesCampaignWizard } from '../../components/SalesCampaignWizard';
import { BibsLeadSourcePanel } from '../../components/BibsLeadSourcePanel';
import { AllstateCallingPanel, type CallingWorkspace } from '../../components/AllstateCallingPanel';

type CapabilitiesResponse = { hermes?: ConnectorCapabilities };
type Company = { id: string; name: string; status: string };
type Campaign = {
  id: string;
  company_id: string;
  name: string;
  industry?: string;
  geographic_area?: string;
  target_audience?: string;
  description?: string;
  daily_lead_goal?: number;
  daily_email_goal?: number;
  daily_email_limit?: number;
  campaign_type?: string;
  provisioning_state?: string;
  provisioning_result?: Record<string, unknown>;
  timezone?: string;
  report_recipient?: string;
  dry_run_mode?: boolean;
  status: string;
};
type Job = { campaign_id?: string | null; status: string; task_type: string };
type Employee = { id: string; campaign_id?: string | null; name: string; employee_type: string; hermes_job_id?: string | null; status: string };
type Schedule = { id: string; employee_id: string; name: string; cron: string; timezone: string; is_paused: boolean; last_run_at?: string | null; next_run_at?: string | null; payload?: Record<string, unknown> };
type LeadSchema = { locked_fields?: string[]; custom_fields?: Array<{ name: string; label?: string; hidden?: boolean; order?: number }>; columns?: string[] };
type LeadOutputs = { outputs: Array<{ path: string; file_name?: string; download_url: string; row_count: number; generated_at: string; modified_at?: string; columns?: string[]; kind?: string }>; rows: Record<string, unknown>[] };

function countJobs(jobs: Job[], campaignId: string, task?: string) {
  return jobs.filter((job) => job.campaign_id === campaignId && (!task || job.task_type === task)).length;
}

function primaryEmployee(employees: Employee[], campaignId: string) {
  return employees.find((employee) => employee.campaign_id === campaignId && employee.status !== 'Archived' && ['Email Outreach', 'Lead Researcher', 'CRM Manager'].includes(employee.employee_type))
    || employees.find((employee) => employee.campaign_id === campaignId && employee.status !== 'Archived');
}

function isLeadResearchEmployee(employee?: Employee, campaign?: Campaign) {
  return employee?.employee_type === 'Lead Researcher' || campaign?.campaign_type === 'lead_generation' || /lead research|lead generation/i.test(campaign?.name || '');
}

function isEmailOutreachEmployee(employee?: Employee, campaign?: Campaign) {
  return ['Email Outreach', 'Draft Writer', 'Outreach Draft Writer', 'Email Sender'].includes(employee?.employee_type || '') || /outreach|email/i.test(campaign?.name || '');
}

function isReportingEmployee(employee?: Employee, campaign?: Campaign) {
  return ['CRM Manager', 'Report Manager', 'Daily Reporter'].includes(employee?.employee_type || '') || /report/i.test(campaign?.name || '');
}

function leadSourceCampaignFor(campaigns: Campaign[], companyId: string, targetCampaignId?: string) {
  const candidates = campaigns.filter((campaign) => campaign.company_id === companyId && campaign.id !== targetCampaignId);
  const searchable = (campaign: Campaign) => `${campaign.id || ''} ${campaign.name || ''} ${campaign.campaign_type || ''}`;
  return candidates.find((campaign) => /lead[-_ ]research|lead[-_ ]generation/i.test(searchable(campaign)))
    || candidates.find((campaign) => campaign.campaign_type === 'lead_generation');
}

function currentBlocker(campaign: Campaign, employee?: Employee) {
  if (!employee) return 'Create or provision an AI Sales Employee for this campaign.';
  if (!employee.hermes_job_id) return 'Employee is not connected to Hermes yet.';
  if (campaign.dry_run_mode !== false && Number(campaign.daily_email_goal || 0) > 0) return 'Email sending is disabled until sender, compliance and internal-test readiness pass.';
  if (employee.status === 'Paused') return 'Employee schedule is paused.';
  return 'No current blocker detected.';
}

function campaignGoal(campaign: Campaign) {
  const target = campaign.target_audience || 'target customers';
  const area = campaign.geographic_area || 'selected market';
  const industry = campaign.industry || 'selected niche';
  return `${campaign.name}: find and convert ${target} in ${area} for ${industry}.`;
}

export default async function CampaignsPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const companyQuery = queryString({ company_id: companyId || undefined });
  const [campaigns, jobs, employees, schedules, capabilitiesResponse] = companyId
    ? await Promise.all([
        serverApi<Campaign[]>(`/campaigns${companyQuery}`, []),
        serverApi<Job[]>(`/jobs${companyQuery}`, []),
        serverApi<Employee[]>(`/employees${companyQuery}`, []),
        serverApi<Schedule[]>(`/schedules${companyQuery}`, []),
        serverApi<CapabilitiesResponse>('/connectors/capabilities', {}),
      ])
    : [[], [], [], [], {}] as [Campaign[], Job[], Employee[], Schedule[], CapabilitiesResponse];
  const capabilities = capabilitiesResponse.hermes || defaultConnectorCapabilities;
  const leadDetails = companyId
    ? Object.fromEntries(await Promise.all(campaigns.map(async (campaign) => {
        const [schema, outputs] = await Promise.all([
          serverApi<LeadSchema>(`/campaigns/${campaign.id}/lead-schema`, {}),
          serverApi<LeadOutputs>(`/campaigns/${campaign.id}/lead-outputs`, { outputs: [], rows: [] }),
        ]);
        return [campaign.id, { schema, outputs }];
      })))
    : {};
  const allstateCallingWorkspace = companyId === 'company-allstate-himanshu'
    ? await serverApi<CallingWorkspace>('/calling/allstate', { confirmation_required: '', settings: {}, health: {}, attempts: [] })
    : null;
  const companyName = new Map(companies.map((company) => [company.id, company.name]));
  const statusRank = (status: string) => ({ Running: 0, Scheduled: 1, Paused: 2, Stopped: 3, Error: 4 }[status] ?? 5);
  const hermesIdsByCampaign = new Map<string, string[]>();
  campaigns.forEach((campaign) => {
    const values = employees
      .filter((employee) => employee.campaign_id === campaign.id && employee.hermes_job_id && employee.status !== 'Archived')
      .sort((a, b) => statusRank(a.status) - statusRank(b.status) || a.name.localeCompare(b.name))
      .map((employee) => `${employee.employee_type}: ${employee.hermes_job_id}`);
    const unique = Array.from(new Set(values));
    hermesIdsByCampaign.set(campaign.id, unique.length > 4 ? [...unique.slice(0, 4), `+${unique.length - 4} more`] : unique);
  });
  const companyOptions = companies.filter((company) => company.status !== 'Archived').map((company) => ({ value: company.id, label: company.name }));
  const hasEmailWorkflow = campaigns.some((campaign) => isEmailOutreachEmployee(primaryEmployee(employees, campaign.id), campaign));
  const displayCampaigns = campaigns.filter((campaign) => {
    const employee = primaryEmployee(employees, campaign.id);
    return !(hasEmailWorkflow && (isLeadResearchEmployee(employee, campaign) || isReportingEmployee(employee, campaign)));
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-zinc-500">Company &gt; Sales Workspace &gt; AI Sales Employee OS</p>
          <h1 className="text-2xl font-semibold">Sales Workspace Control Center</h1>
        </div>
        <div className="text-sm text-zinc-400">{displayCampaigns.length} sales workflows</div>
      </div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} label="Company" />
      {!companyId ? <div className="card text-sm text-amber-300">Select a company to manage campaigns.</div> : null}
      {companyId ? <SalesCampaignWizard companyId={companyId} companies={companies} /> : null}
      {companyId === 'company-brew-it-by-sash' ? <BibsLeadSourcePanel companyId={companyId} leadCampaignId="campaign-brew-it-by-sash-lead-research" /> : null}
      <div className="grid gap-3 md:grid-cols-4">
        <div className="card"><p className="text-sm text-zinc-400">Active sales employees</p><p className="mt-2 text-3xl font-semibold">{employees.filter((employee) => employee.status !== 'Archived').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Lead research runs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.task_type === 'Generate Leads').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Controlled email runs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.task_type === 'Controlled Outreach Batch' || job.task_type === 'Send Outreach').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Needs attention</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.status === 'Failed' || job.status === 'Blocked').length}</p></div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Sales campaign</th><th>Company</th><th>AI employee</th><th>Goal</th><th>Daily targets</th><th>Status</th></tr></thead>
          <tbody>
            {displayCampaigns.map((campaign) => {
              const employee = primaryEmployee(employees, campaign.id);
              const leadSourceCampaign = isEmailOutreachEmployee(employee, campaign) ? leadSourceCampaignFor(campaigns, campaign.company_id, campaign.id) : undefined;
              return (
              <tr key={campaign.id}>
                <td className="font-medium text-stone-100">{leadSourceCampaign ? 'Email Marketing Campaign' : campaign.name}</td>
                <td>{companyName.get(campaign.company_id) || campaign.company_id}</td>
                <td>{leadSourceCampaign ? 'Lead generation + email drafting + reporting' : employee ? `${employee.name} / ${employee.employee_type}` : 'No AI employee yet'}</td>
                <td>
                  <div className="max-w-md text-xs text-zinc-400">
                    <div>{campaign.industry || 'Industry missing'} / {campaign.geographic_area || 'Location missing'}</div>
                    <div>{campaign.target_audience || 'Target customer not set'}</div>
                  </div>
                </td>
                <td>{campaign.daily_lead_goal ?? 0} leads, {campaign.daily_email_goal ?? 0} emails</td>
                <td>{campaign.status}</td>
              </tr>
            );})}
            {!displayCampaigns.length ? <tr><td colSpan={6} className="text-zinc-400">{companyId ? 'No campaigns for selected company' : 'No company selected'}</td></tr> : null}
          </tbody>
        </table>
      </div>
      {companyId ? (
        <div className="space-y-3">
          {displayCampaigns.map((campaign) => {
            const detail = (leadDetails as Record<string, { schema: LeadSchema; outputs: LeadOutputs }>)[campaign.id] || { schema: {}, outputs: { outputs: [], rows: [] } };
            const employee = primaryEmployee(employees, campaign.id);
            const isLeadResearch = isLeadResearchEmployee(employee, campaign);
            const isEmailOutreach = isEmailOutreachEmployee(employee, campaign);
            const isReporting = isReportingEmployee(employee, campaign);
            const leadSourceCampaign = isEmailOutreach ? leadSourceCampaignFor(campaigns, campaign.company_id, campaign.id) : undefined;
            const leadSourceDetail = leadSourceCampaign ? (leadDetails as Record<string, { schema: LeadSchema; outputs: LeadOutputs }>)[leadSourceCampaign.id] || { schema: {}, outputs: { outputs: [], rows: [] } } : null;
            const reportCampaign = campaigns.find((item) => item.company_id === campaign.company_id && isReportingEmployee(primaryEmployee(employees, item.id), item));
            const campaignSchedules = schedules.filter((schedule) => employees.some((item) => item.id === schedule.employee_id && item.campaign_id === campaign.id));
            const nextSchedule = campaignSchedules.find((schedule) => !schedule.is_paused) || campaignSchedules[0];
            const workflowTitle = leadSourceCampaign ? 'Email Marketing Campaign' : employee?.name || 'AI Sales Employee';
            return (
              <div className="card" key={campaign.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm text-zinc-500">Company &gt; {companyName.get(campaign.company_id) || campaign.company_id} &gt; {workflowTitle}</p>
                    <h2 className="text-xl font-semibold">{workflowTitle}</h2>
                    <p className="text-sm text-zinc-400">{campaignGoal(campaign)}</p>
                    <p className="mt-1 text-xs text-zinc-500">Current blocker: {currentBlocker(campaign, employee)}</p>
                  </div>
                  <div className="text-right text-xs text-zinc-400">
                    <div>Status: <span className="text-zinc-100">{employee?.status || campaign.status}</span></div>
                    <div>Next scheduled action: <LocalTime value={nextSchedule?.next_run_at} /></div>
                  </div>
                </div>
                <div className="mt-4 grid gap-3" data-voryx-campaign-detail-sections>
                  <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Goal</h3>
                    <p className="mt-1 text-xs text-zinc-400">{campaign.industry || 'Industry missing'} / {campaign.geographic_area || 'Location missing'} / {campaign.target_audience || 'Target customer missing'}</p>
                    <p className="mt-1 text-xs text-zinc-400">{campaign.description || 'Offer/product and notes not set'}</p>
                  </section>
                  <section className="rounded border border-zinc-800 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold">Overview</h3>
                        <p className="mt-1 text-xs text-zinc-400">Sales Campaign / Overview / Leads / Channels / Activity / Appointments / Reports / Settings</p>
                      </div>
                      <a className="btn-secondary text-xs" href={`/reports${queryString({ company_id: campaign.company_id })}`}>Reports</a>
                    </div>
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <div className="rounded border border-zinc-900 p-3">
                        <div className="flex items-center justify-between gap-3">
                          <h4 className="text-sm font-semibold">Email</h4>
                          <span className="text-xs text-emerald-300">{isEmailOutreach || leadSourceCampaign ? 'Connected' : 'Not connected'}</span>
                        </div>
                        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-zinc-400">
                          <div><dt>Eligible leads</dt><dd className="text-lg text-zinc-100">{(leadSourceDetail?.outputs.rows || detail.outputs.rows || []).length}</dd></div>
                          <div><dt>Approved</dt><dd className="text-lg text-zinc-100">{jobs.filter((job) => job.campaign_id === campaign.id && /approve/i.test(job.task_type)).length}</dd></div>
                          <div><dt>Ready to send</dt><dd className="text-lg text-zinc-100">{jobs.filter((job) => job.campaign_id === campaign.id && job.status === 'Queued').length}</dd></div>
                          <div><dt>Sent</dt><dd className="text-lg text-zinc-100">{jobs.filter((job) => job.campaign_id === campaign.id && job.task_type === 'Controlled Outreach Batch' && job.status === 'Completed').length}</dd></div>
                          <div><dt>Replies</dt><dd className="text-lg text-zinc-100">0</dd></div>
                        </dl>
                      </div>
                      <a className="rounded border border-zinc-900 p-3 transition hover:border-zinc-700" href={campaign.id === 'campaign-allstate-quote-calling' ? '#calling-channel' : '/calling'}>
                        <div className="flex items-center justify-between gap-3">
                          <h4 className="text-sm font-semibold">Calling</h4>
                          <span className={campaign.id === 'campaign-allstate-quote-calling' ? 'text-xs text-emerald-300' : 'text-xs text-zinc-500'}>{campaign.id === 'campaign-allstate-quote-calling' ? 'Connected' : 'Not connected'}</span>
                        </div>
                        <dl className="mt-3 grid grid-cols-2 gap-2 text-xs text-zinc-400">
                          <div><dt>Phone-ready leads</dt><dd className="text-lg text-zinc-100">0</dd></div>
                          <div><dt>Consent verified</dt><dd className="text-lg text-zinc-100">{campaign.id === 'campaign-allstate-quote-calling' ? 1 : 0}</dd></div>
                          <div><dt>Approved</dt><dd className="text-lg text-zinc-100">0</dd></div>
                          <div><dt>Ready to call</dt><dd className="text-lg text-zinc-100">0</dd></div>
                          <div><dt>Calls completed</dt><dd className="text-lg text-zinc-100">{campaign.id === 'campaign-allstate-quote-calling' ? (allstateCallingWorkspace?.attempts || []).filter((attempt) => ['ended', 'analyzed'].includes(attempt.status)).length : 0}</dd></div>
                          <div><dt>Appointments</dt><dd className="text-lg text-zinc-100">0</dd></div>
                        </dl>
                      </a>
                    </div>
                  </section>
                  {isLeadResearch ? <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Leads</h3>
                    <LeadSchemaEditor campaignId={campaign.id} initialSchema={detail.schema || {}} />
                  </section> : null}
                  {isLeadResearch || leadSourceCampaign ? <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Lead Source</h3>
                    <p className="mb-2 text-xs text-zinc-400">Current unique lead source: {leadSourceCampaign?.name || campaign.name}. Historical review decisions are separate from the current lead pool.</p>
                    <LeadOutputsPanel outputs={(leadSourceDetail?.outputs.outputs || detail.outputs.outputs) || []} rows={(leadSourceDetail?.outputs.rows || detail.outputs.rows) || []} />
                  </section> : null}
                  {isLeadResearch || isEmailOutreach ? <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">{isLeadResearch && !isEmailOutreach ? 'Lead Research Workflow' : 'Email Sending Workflow'}</h3>
                    <OutreachControlsPanel
                      companyId={campaign.company_id}
                      campaignId={campaign.id}
                      mode={isLeadResearch && !isEmailOutreach ? 'lead_research' : 'email_outreach'}
                      leadSourceCampaignId={leadSourceCampaign?.id}
                      reportHref={`/reports${queryString({ company_id: campaign.company_id })}`}
                    />
                  </section> : null}
                  <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Replies and Meetings</h3>
                    <p className="mt-1 text-xs text-zinc-400">Reply Monitor: not connected. Follow-up is blocked until Gmail thread monitoring, bounces, unsubscribes and reply classification are connected.</p>
                    <p className="mt-1 text-xs text-zinc-400">Meetings booked: 0. Appointment booking will activate only after reply monitor and calendar policy are connected.</p>
                  </section>
                  <section id="calling-channel" className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Calling</h3>
                    {campaign.id === 'campaign-allstate-quote-calling' && allstateCallingWorkspace ? (
                      <div className="mt-3"><AllstateCallingPanel initialWorkspace={allstateCallingWorkspace} /></div>
                    ) : (
                      <p className="mt-1 text-xs text-zinc-400">Status: not connected. Required before enabling: voice provider, caller ID, call script, recording/transcript policy, do-not-call controls and daily call limit.</p>
                    )}
                  </section>
                  <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">SMS/Text</h3>
                    <p className="mt-1 text-xs text-zinc-400">Status: not connected. Required before enabling: SMS provider, opt-out compliance, phone verification and daily SMS limit.</p>
                  </section>
                  <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Social Outreach</h3>
                    <p className="mt-1 text-xs text-zinc-400">Status: not connected. Required before enabling: social account/API, profile finder, DM policy and anti-spam controls.</p>
                  </section>
                  <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">WhatsApp</h3>
                    <p className="mt-1 text-xs text-zinc-400">Status: not connected. Required before enabling: WhatsApp Business integration, template approval and opt-out handling.</p>
                  </section>
                  {isReporting || leadSourceCampaign ? <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Daily Report</h3>
                    <p className="mt-1 text-xs text-zinc-400">Business-readable daily reports summarize what happened today, leads found, drafts created, emails sent, replies, meetings, blockers, next recommended action and files.</p>
                    <div className="mt-3">
                      <DailyReportPanel initialReport={null} initialText="" />
                    </div>
                  </section> : <section className="rounded border border-zinc-800 p-3">
                    <h3 className="text-sm font-semibold">Daily Report</h3>
                    <p className="mt-1 text-xs text-zinc-400">Reports are controlled by the CRM Manager / Daily Reporter employee.</p>
                    <a className="btn-secondary mt-2 inline-flex text-xs" href={`/reports${queryString({ company_id: campaign.company_id })}`}>Open reports</a>
                  </section>}
                  <details className="rounded border border-zinc-800 p-3">
                    <summary className="cursor-pointer text-sm font-semibold">Advanced</summary>
                    <div className="mt-3 grid gap-3">
                      <section className="rounded border border-zinc-900 p-3">
                        <h3 className="text-sm font-semibold">Hermes Sync and Job IDs</h3>
                        <p className="mt-1 text-xs text-zinc-400">{(hermesIdsByCampaign.get(campaign.id) || []).join(' / ') || String(campaign.provisioning_result?.hermes_job_id || 'No Hermes job')}</p>
                        <p className="mt-1 text-xs text-zinc-500">Provisioning: {campaign.provisioning_state || 'Draft'} / Imported jobs: {countJobs(jobs, campaign.id)} total</p>
                      </section>
                      <section className="rounded border border-zinc-900 p-3">
                        <h3 className="text-sm font-semibold">Raw Schedules</h3>
                        <div className="mt-2 grid gap-1 text-xs text-zinc-400">{campaignSchedules.map((schedule) => <div key={schedule.id}>{schedule.name} / {schedule.is_paused ? 'Paused' : 'Active'} / {schedule.cron} / next <LocalTime value={schedule.next_run_at} /></div>)}</div>
                      </section>
                      <section className="rounded border border-zinc-900 p-3">
                        <h3 className="text-sm font-semibold">Raw Employees</h3>
                        <div className="mt-2 grid gap-1 text-xs text-zinc-400">{employees.filter((item) => item.campaign_id === campaign.id && item.status !== 'Archived').map((item) => <div key={item.id}>{item.name} / {item.employee_type} / {item.status} / {item.hermes_job_id || 'no Hermes job'}</div>)}</div>
                      </section>
                      <ModelPolicyPanel scope="company" companyId={campaign.company_id} title="Model Policy" compact />
                    </div>
                  </details>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
      {companyId ? (
        <details className="card" data-voryx-advanced-raw-campaign-editor>
          <summary className="cursor-pointer text-sm font-semibold">Advanced: raw campaign records</summary>
          <div className="mt-3">
        <CrudPage
          title="Advanced Campaign Records"
          path="/campaigns"
          initialItems={campaigns}
          query={{ company_id: companyId }}
          createLabel="Create Sales Campaign"
          emptyLabel="No campaigns for selected company"
          displayMaps={{ company_id: Object.fromEntries(companies.map((company) => [company.id, company.name])) }}
          fields={{
            company_id: { type: 'select', label: 'Company', options: companyOptions },
            industry: { type: 'text', label: 'Industry / niche *' },
            geographic_area: { type: 'text', label: 'City / region *' },
            target_audience: { type: 'textarea', label: 'Target customer *' },
            description: { type: 'textarea', label: 'Offer/product, exclusions, tone, channels and notes' },
            lead_source_type: { type: 'select', label: 'Lead source *', options: [
              { value: 'ai_internet_research', label: 'AI Internet Research - generate leads from internet' },
              { value: 'uploaded_seed_csv', label: 'Upload CSV' },
              { value: 'existing_lead_pool', label: 'Use existing lead pool' },
              { value: 'another_campaign', label: 'Use leads from another campaign' },
              { value: 'source_urls', label: 'Manual source URLs' },
              { value: 'search_queries', label: 'Manual search queries' },
            ] },
            lead_source_file: { type: 'text', label: 'Optional upload CSV (/opt/data/...)' },
            lead_source_url: { type: 'text', label: 'Optional reference URL' },
            lead_source_query: { type: 'text', label: 'Optional manual search query / source notes' },
            daily_lead_goal: { type: 'number', label: 'Lead goal *' },
            daily_email_goal: { type: 'number', label: 'Daily email goal' },
            daily_email_limit: { type: 'number', label: 'Max emails per day' },
            allowed_sending_days: { type: 'days', label: 'Allowed sending days' },
            allowed_sending_hours: { type: 'hours', label: 'Allowed sending hours' },
            dry_run_mode: { type: 'boolean', label: 'Email sending disabled *' },
            campaign_type: { type: 'select', label: 'Campaign blueprint', options: [
              { value: 'sales_outreach', label: 'B2B Sales Campaign - email now, calls/text/social later' },
              { value: 'lead_generation', label: 'Lead Generation Source Only' },
              { value: 'custom', label: 'Custom Campaign' },
            ] },
            provisioning_state: { type: 'readonly', label: 'Provisioning state', readOnly: true },
            provisioning_result: { type: 'json', label: 'Provisioning result', readOnly: true },
            start_date: { type: 'date' },
            end_date: { type: 'date' },
            status: { type: 'select', options: [{ value: 'Active', label: 'Active' }, { value: 'Inactive', label: 'Inactive' }, { value: 'Archived', label: 'Archived' }] },
          }}
          capabilities={capabilities}
          defaults={{
            company_id: companyId,
            name: '',
            description: '',
            lead_source_type: 'ai_internet_research',
            lead_source_file: '',
            lead_source_url: '',
            lead_source_query: '',
            industry: '',
            target_audience: '',
            geographic_area: '',
            daily_lead_goal: 5,
            daily_email_goal: 0,
            daily_email_limit: 0,
            campaign_type: 'sales_outreach',
            provisioning_state: 'Draft',
            provisioning_result: {},
            timezone: 'America/Toronto',
            allowed_sending_days: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
            allowed_sending_hours: { start: '09:00', end: '19:00' },
            internal_test_recipient: 'himanshusoni3214@gmail.com',
            report_recipient: 'himanshusoni3214@gmail.com',
            dry_run_mode: true,
            start_date: '',
            end_date: '',
            status: 'Active',
          }}
        />
          </div>
        </details>
      ) : null}
    </div>
  );
}
