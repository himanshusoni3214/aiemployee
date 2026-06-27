import { serverApi } from '../../lib/serverApi';
import CrudPage from '../../components/CrudPage';

type Company = { id: string; name: string };
type Campaign = {
  id: string;
  company_id: string;
  name: string;
  industry?: string;
  daily_lead_goal?: number;
  daily_email_goal?: number;
  status: string;
};
type Job = { campaign_id?: string | null; status: string; task_type: string };

function countJobs(jobs: Job[], campaignId: string, task?: string) {
  return jobs.filter((job) => job.campaign_id === campaignId && (!task || job.task_type === task)).length;
}

export default async function CampaignsPage() {
  const [campaigns, companies, jobs] = await Promise.all([
    serverApi<Campaign[]>('/campaigns', []),
    serverApi<Company[]>('/companies', []),
    serverApi<Job[]>('/jobs', []),
  ]);
  const companyName = new Map(companies.map((company) => [company.id, company.name]));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Campaigns</h1>
        <div className="text-sm text-zinc-400">{campaigns.length} campaigns</div>
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        <div className="card"><p className="text-sm text-zinc-400">Active</p><p className="mt-2 text-3xl font-semibold">{campaigns.filter((campaign) => campaign.status === 'Active').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Outreach Jobs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.task_type === 'Send Outreach').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Lead Jobs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.task_type === 'Generate Leads').length}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Failed Jobs</p><p className="mt-2 text-3xl font-semibold">{jobs.filter((job) => job.status === 'Failed').length}</p></div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Campaign</th><th>Company</th><th>Industry</th><th>Daily Goals</th><th>Imported Jobs</th><th>Status</th></tr></thead>
          <tbody>
            {campaigns.map((campaign) => (
              <tr key={campaign.id}>
                <td className="font-medium text-stone-100">{campaign.name}</td>
                <td>{companyName.get(campaign.company_id) || campaign.company_id}</td>
                <td>{campaign.industry || '-'}</td>
                <td>{campaign.daily_lead_goal ?? 0} leads, {campaign.daily_email_goal ?? 0} emails</td>
                <td>{countJobs(jobs, campaign.id)} total / {countJobs(jobs, campaign.id, 'Send Outreach')} outreach</td>
                <td>{campaign.status}</td>
              </tr>
            ))}
            {!campaigns.length ? <tr><td colSpan={6} className="text-zinc-400">No campaigns imported from Hermes yet</td></tr> : null}
          </tbody>
        </table>
      </div>
      <CrudPage title="Campaign Management" path="/campaigns" defaults={{
        company_id: companies[0]?.id || '',
        name: '',
        description: '',
        industry: '',
        target_audience: '',
        geographic_area: '',
        daily_lead_goal: 0,
        daily_email_goal: 0,
        daily_email_limit: 0,
        timezone: 'America/Toronto',
        allowed_sending_days: ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'],
        allowed_sending_hours: { start: '09:00', end: '19:00' },
        internal_test_recipient: 'himanshusoni3214@gmail.com',
        report_recipient: 'himanshusoni3214@gmail.com',
        dry_run_mode: true,
        start_date: '',
        end_date: '',
        status: 'Active',
      }} />
    </div>
  );
}
