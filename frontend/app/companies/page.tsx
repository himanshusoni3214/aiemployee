import { serverApi } from '../../lib/serverApi';
import CrudPage from '../../components/CrudPage';

type Company = {
  id: string;
  name: string;
  logo?: string | null;
  website?: string | null;
  industry?: string | null;
  status: string;
};
type Campaign = { id: string; company_id: string; status: string };
type Employee = { id: string; company_id: string; status: string };
type Job = { campaign_id?: string | null; status: string };

function companyJobs(jobs: Job[], campaigns: Campaign[], companyId: string) {
  const campaignIds = new Set(campaigns.filter((campaign) => campaign.company_id === companyId).map((campaign) => campaign.id));
  return jobs.filter((job) => job.campaign_id && campaignIds.has(job.campaign_id));
}

export default async function CompaniesPage() {
  const [companies, campaigns, employees, jobs] = await Promise.all([
    serverApi<Company[]>('/companies', []),
    serverApi<Campaign[]>('/campaigns', []),
    serverApi<Employee[]>('/employees', []),
    serverApi<Job[]>('/jobs', []),
  ]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">Companies</h1>
        <div className="text-sm text-zinc-400">{companies.length} companies</div>
      </div>
      <div className="table-wrap">
        <table className="ops-table">
          <thead><tr><th>Company</th><th>Industry</th><th>Website</th><th>Campaigns</th><th>Workers</th><th>Jobs</th><th>Status</th></tr></thead>
          <tbody>
            {companies.map((company) => {
              const rows = companyJobs(jobs, campaigns, company.id);
              return (
                <tr key={company.id}>
                  <td className="font-medium text-stone-100">{company.name}</td>
                  <td>{company.industry || '-'}</td>
                  <td>{company.website ? <a className="text-emerald-300" href={company.website}>{company.website}</a> : '-'}</td>
                  <td>{campaigns.filter((campaign) => campaign.company_id === company.id).length}</td>
                  <td>{employees.filter((employee) => employee.company_id === company.id).length}</td>
                  <td>{rows.length} total / {rows.filter((job) => job.status === 'Failed').length} failed</td>
                  <td>{company.status}</td>
                </tr>
              );
            })}
            {!companies.length ? <tr><td colSpan={7} className="text-zinc-400">No companies imported from Hermes yet</td></tr> : null}
          </tbody>
        </table>
      </div>
      <CrudPage title="Company Management" path="/companies" defaults={{
        name: '',
        logo: '',
        website: '',
        industry: '',
        status: 'Active',
        timezone: 'America/Toronto',
        default_report_recipient: 'himanshusoni3214@gmail.com',
        daily_email_limit: 50,
        notes: '',
      }} />
    </div>
  );
}
