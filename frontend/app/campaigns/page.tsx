import { serverApi } from '../../lib/serverApi';
import CrudPage from '../../components/CrudPage';
import { defaultConnectorCapabilities, type ConnectorCapabilities } from '../../components/ActionButtons';
import { CompanySelector } from '../../components/CompanySelector';
import { LeadOutputsPanel } from '../../components/LeadOutputsPanel';
import { OutreachControlsPanel } from '../../components/OutreachControlsPanel';
import { LeadSchemaEditor } from '../../components/LeadSchemaEditor';
import { queryString, selectedCompanyId } from '../../lib/companySelection';

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
type LeadSchema = { locked_fields?: string[]; custom_fields?: Array<{ name: string; label?: string; hidden?: boolean; order?: number }>; columns?: string[] };
type LeadOutputs = { outputs: Array<{ path: string; file_name?: string; download_url: string; row_count: number; generated_at: string; modified_at?: string; columns?: string[]; kind?: string }>; rows: Record<string, unknown>[] };

function countJobs(jobs: Job[], campaignId: string, task?: string) {
  return jobs.filter((job) => job.campaign_id === campaignId && (!task || job.task_type === task)).length;
}

export default async function CampaignsPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const companyQuery = queryString({ company_id: companyId || undefined });
  const [campaigns, jobs, employees, capabilitiesResponse] = companyId
    ? await Promise.all([
        serverApi<Campaign[]>(`/campaigns${companyQuery}`, []),
        serverApi<Job[]>(`/jobs${companyQuery}`, []),
        serverApi<Employee[]>(`/employees${companyQuery}`, []),
        serverApi<CapabilitiesResponse>('/connectors/capabilities', {}),
      ])
    : [[], [], [], {}] as [Campaign[], Job[], Employee[], CapabilitiesResponse];
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

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-zinc-500">Companies &gt; {companyId ? companyName.get(companyId) : 'Select Company'} &gt; Campaigns</p>
          <h1 className="text-2xl font-semibold">Campaigns</h1>
        </div>
        <div className="text-sm text-zinc-400">{campaigns.length} campaigns</div>
      </div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} label="Company" />
      {!companyId ? <div className="card text-sm text-amber-300">Select a company to manage campaigns.</div> : null}
      <div className="grid gap-3 md:grid-cols-4">
        <div className="card"><p className="text-sm text-zinc-400">Active</p><p className="mt-2 text-3xl font-semibold">{campaigns.filter((campaign) => campaign.status === 'Active').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Outreach Jobs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.task_type === 'Send Outreach').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Lead Jobs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.task_type === 'Generate Leads').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Failed Jobs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.status === 'Failed').length}</p></div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Campaign</th><th>Company</th><th>Blueprint</th><th>Target Definition</th><th>Provisioning</th><th>Daily Goals</th><th>Imported Jobs</th><th>Status</th></tr></thead>
          <tbody>
            {campaigns.map((campaign) => (
              <tr key={campaign.id}>
                <td className="font-medium text-stone-100">{campaign.name}</td>
                <td>{companyName.get(campaign.company_id) || campaign.company_id}</td>
                <td>{(campaign.campaign_type || 'custom').replaceAll('_', ' ')}</td>
                <td>
                  <div className="max-w-md text-xs text-zinc-400">
                    <div>{campaign.industry || 'Industry missing'} / {campaign.geographic_area || 'Location missing'}</div>
                    <div>{campaign.target_audience || 'Target customer not set'}</div>
                    <div>{campaign.description || 'Offer/product and notes not set'}</div>
                    <div>{campaign.dry_run_mode === false ? 'Email sending enabled' : 'Lead generation only. Email sending disabled.'}</div>
                    <div>Report: {campaign.report_recipient || 'not set'} / {campaign.timezone || 'America/Toronto'}</div>
                  </div>
                </td>
                <td>
                  <div>{campaign.provisioning_state || 'Draft'}</div>
                  <div className="text-xs text-zinc-500">{(hermesIdsByCampaign.get(campaign.id) || []).join(' / ') || String(campaign.provisioning_result?.hermes_job_id || '') || 'No Hermes job'}</div>
                  <div className="text-xs text-zinc-500">{String(campaign.provisioning_result?.approved_script || '').includes('voryx_generic_lead_research.py') ? 'Generic lead script' : ''}</div>
                </td>
                <td>{campaign.daily_lead_goal ?? 0} leads, {campaign.daily_email_goal ?? 0} emails</td>
                <td>{countJobs(jobs, campaign.id)} total / {countJobs(jobs, campaign.id, 'Send Outreach')} outreach</td>
                <td>{campaign.status}</td>
              </tr>
            ))}
            {!campaigns.length ? <tr><td colSpan={8} className="text-zinc-400">{companyId ? 'No campaigns for selected company' : 'No company selected'}</td></tr> : null}
          </tbody>
        </table>
      </div>
      {companyId ? (
        <div className="space-y-3">
          {campaigns.map((campaign) => {
            const detail = (leadDetails as Record<string, { schema: LeadSchema; outputs: LeadOutputs }>)[campaign.id] || { schema: {}, outputs: { outputs: [], rows: [] } };
            return (
              <div className="card" key={campaign.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold">{campaign.name}</h2>
                    <p className="text-sm text-zinc-400">Lead Sheet Fields and Generated Files. Locked fields are preserved; custom fields save to PostgreSQL and workspace config.</p>
                    <p className="mt-1 text-xs text-zinc-500">Add Lead Researcher / Add Daily Reporter / Add Outreach Draft Writer from AI Employees. Reply Handler and Voice Agent are not connected.</p>
                  </div>
                  <div className="text-xs text-zinc-500">{(hermesIdsByCampaign.get(campaign.id) || []).join(' / ') || String(campaign.provisioning_result?.hermes_job_id || 'No Hermes job')}</div>
                </div>
                <LeadOutputsPanel outputs={detail.outputs.outputs || []} rows={detail.outputs.rows || []} />
                <OutreachControlsPanel companyId={campaign.company_id} campaignId={campaign.id} />
                <LeadSchemaEditor campaignId={campaign.id} initialSchema={detail.schema || {}} />
              </div>
            );
          })}
        </div>
      ) : null}
      {companyId ? (
        <CrudPage
          title="Campaign Management"
          path="/campaigns"
          initialItems={campaigns}
          query={{ company_id: companyId }}
          createLabel="Create Campaign"
          emptyLabel="No campaigns for selected company"
          displayMaps={{ company_id: Object.fromEntries(companies.map((company) => [company.id, company.name])) }}
          fields={{
            company_id: { type: 'select', label: 'Company', options: companyOptions },
            industry: { type: 'text', label: 'Industry / niche *' },
            geographic_area: { type: 'text', label: 'City / region *' },
            target_audience: { type: 'textarea', label: 'Target customer *' },
            description: { type: 'textarea', label: 'Offer/product, exclusions, tone and notes' },
            lead_source_type: { type: 'select', label: 'Lead source *', options: [
              { value: 'uploaded_seed_csv', label: 'Uploaded seed CSV' },
              { value: 'existing_legacy_file', label: 'Existing legacy file' },
              { value: 'manual_import', label: 'Manual import CSV' },
              { value: 'real_directory', label: 'Real directory (not configured)' },
            ] },
            lead_source_file: { type: 'text', label: 'Lead source file (/opt/data/...)' },
            lead_source_url: { type: 'text', label: 'Lead source URL' },
            lead_source_query: { type: 'text', label: 'Search query / source query' },
            daily_lead_goal: { type: 'number', label: 'Lead goal *' },
            daily_email_goal: { type: 'number', label: 'Daily email goal' },
            daily_email_limit: { type: 'number', label: 'Max emails per day' },
            allowed_sending_days: { type: 'days', label: 'Allowed sending days' },
            allowed_sending_hours: { type: 'hours', label: 'Allowed sending hours' },
            dry_run_mode: { type: 'boolean', label: 'Email sending disabled *' },
            campaign_type: { type: 'select', label: 'Campaign blueprint', options: [
              { value: 'sales_outreach', label: 'Sales / Outreach Campaign' },
              { value: 'lead_generation', label: 'Lead Generation Campaign' },
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
            lead_source_type: '',
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
      ) : null}
    </div>
  );
}
