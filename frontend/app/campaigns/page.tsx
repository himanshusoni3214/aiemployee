import { serverApi } from '../../lib/serverApi';
import CrudPage from '../../components/CrudPage';
import { defaultConnectorCapabilities, type ConnectorCapabilities } from '../../components/ActionButtons';
import { CompanySelector } from '../../components/CompanySelector';
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
  status: string;
};
type Job = { campaign_id?: string | null; status: string; task_type: string };

function countJobs(jobs: Job[], campaignId: string, task?: string) {
  return jobs.filter((job) => job.campaign_id === campaignId && (!task || job.task_type === task)).length;
}

export default async function CampaignsPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const companyQuery = queryString({ company_id: companyId || undefined });
  const [campaigns, jobs, capabilitiesResponse] = companyId
    ? await Promise.all([
        serverApi<Campaign[]>(`/campaigns${companyQuery}`, []),
        serverApi<Job[]>(`/jobs${companyQuery}`, []),
        serverApi<CapabilitiesResponse>('/connectors/capabilities', {}),
      ])
    : [[], [], {}] as [Campaign[], Job[], CapabilitiesResponse];
  const capabilities = capabilitiesResponse.hermes || defaultConnectorCapabilities;
  const companyName = new Map(companies.map((company) => [company.id, company.name]));
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
          <thead><tr><th>Campaign</th><th>Company</th><th>Template</th><th>Lead Research Config</th><th>Provisioning</th><th>Daily Goals</th><th>Imported Jobs</th><th>Status</th></tr></thead>
          <tbody>
            {campaigns.map((campaign) => (
              <tr key={campaign.id}>
                <td className="font-medium text-stone-100">{campaign.name}</td>
                <td>{companyName.get(campaign.company_id) || campaign.company_id}</td>
                <td>{(campaign.campaign_type || 'custom').replaceAll('_', ' ')}</td>
                <td>
                  {campaign.campaign_type === 'lead_research' ? (
                    <div className="max-w-md text-xs text-zinc-400">
                      <div>{campaign.industry || 'Industry missing'} / {campaign.geographic_area || 'Location missing'}</div>
                      <div>{campaign.target_audience || 'Target customer not set'}</div>
                      <div>Lead generation only. Email sending disabled.</div>
                    </div>
                  ) : '-'}
                </td>
                <td>
                  <div>{campaign.provisioning_state || 'Draft'}</div>
                  <div className="text-xs text-zinc-500">{String(campaign.provisioning_result?.hermes_job_id || '') || 'No Hermes job'}</div>
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
            industry: { type: 'text', label: 'Industry / niche' },
            geographic_area: { type: 'text', label: 'City / region' },
            target_audience: { type: 'textarea', label: 'Target customer' },
            description: { type: 'textarea', label: 'Exclusions / notes' },
            daily_lead_goal: { type: 'number', label: 'Lead count' },
            daily_email_goal: { type: 'number', label: 'Daily email goal' },
            daily_email_limit: { type: 'number', label: 'Max emails per day' },
            allowed_sending_days: { type: 'days', label: 'Allowed sending days' },
            allowed_sending_hours: { type: 'hours', label: 'Allowed sending hours' },
            dry_run_mode: { type: 'boolean', label: 'Email sending disabled' },
            campaign_type: { type: 'select', label: 'Template', options: [
              { value: 'lead_research', label: 'Lead Research' },
              { value: 'daily_reporting', label: 'Daily Reporting' },
              { value: 'outreach_drafting', label: 'Outreach Drafting' },
              { value: 'custom', label: 'Custom' },
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
            industry: '',
            target_audience: '',
            geographic_area: '',
            daily_lead_goal: 5,
            daily_email_goal: 0,
            daily_email_limit: 0,
            campaign_type: 'custom',
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
