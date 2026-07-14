import { serverApi } from '../../lib/serverApi';
import { CompanySelector } from '../../components/CompanySelector';
import { selectedCompanyId, queryString } from '../../lib/companySelection';

type Company = { id: string; name: string; status: string };
type Campaign = { id: string; name: string; company_id: string; campaign_type?: string; status: string };

export default async function OutreachPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const campaigns = companyId ? await serverApi<Campaign[]>(`/campaigns${queryString({ company_id: companyId })}`, []) : [];
  const outreach = campaigns.filter((campaign) => /outreach|email/i.test(`${campaign.id} ${campaign.name} ${campaign.campaign_type || ''}`));
  return (
    <div className="space-y-5">
      <div><p className="text-sm text-zinc-500">Email channel</p><h1 className="text-2xl font-semibold">Outreach</h1><p className="text-sm text-zinc-400">Review approved leads, generate drafts, preview batches, send tests and controlled email batches from the Sales Workspace.</p></div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} label="Company" />
      {outreach.map((campaign) => <a className="card block" href={`/campaigns${queryString({ company_id: campaign.company_id })}`} key={campaign.id}><h2 className="text-lg font-semibold">{campaign.name}</h2><p className="text-sm text-zinc-400">Open Sales Workspace email workflow</p></a>)}
      {companyId && !outreach.length ? <div className="card text-sm text-zinc-400">No email outreach workspace found for this company yet.</div> : null}
    </div>
  );
}
