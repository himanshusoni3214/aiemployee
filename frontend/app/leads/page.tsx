import { serverApi } from '../../lib/serverApi';
import { CompanySelector } from '../../components/CompanySelector';
import { LeadOutputsPanel } from '../../components/LeadOutputsPanel';
import { OutreachControlsPanel } from '../../components/OutreachControlsPanel';
import { queryString, selectedCompanyId } from '../../lib/companySelection';

 type Company = { id: string; name: string; status: string };
 type Campaign = { id: string; company_id: string; name: string; campaign_type?: string; industry?: string; geographic_area?: string; target_audience?: string; daily_lead_goal?: number; status: string };
 type Employee = { id: string; campaign_id?: string | null; name: string; employee_type: string; hermes_job_id?: string | null; status: string };
 type LeadOutputs = { outputs: Array<{ path: string; file_name?: string; download_url: string; row_count: number; generated_at: string; modified_at?: string; columns?: string[]; kind?: string }>; rows: Record<string, unknown>[]; row_count?: number };
 type LeadReview = { items: Array<{ lead_key: string; state: string; can_send: boolean; approval_eligible?: boolean; email_confidence?: string }>; counts: Record<string, number>; eligible_count: number; approval_eligible_count?: number; source_path?: string };

function isLeadCampaign(campaign: Campaign, employees: Employee[]) {
  const employee = employees.find((item) => item.campaign_id === campaign.id && item.status !== 'Archived');
  return campaign.campaign_type === 'lead_generation'
    || campaign.campaign_type === 'lead_research'
    || employee?.employee_type === 'Lead Researcher'
    || /lead research|lead generation/i.test(`${campaign.id} ${campaign.name}`);
}

function countBy(items: LeadReview['items'], key: string, value: string) {
  return items.filter((item) => String((item as any)[key] || '') === value).length;
}

export default async function LeadsPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = (await searchParams) || {};
  const companies = await serverApi<Company[]>('/companies', []);
  const companyId = selectedCompanyId(companies, params.company_id);
  const companyQuery = queryString({ company_id: companyId || undefined });
  const [campaigns, employees] = companyId
    ? await Promise.all([
        serverApi<Campaign[]>(`/campaigns${companyQuery}`, []),
        serverApi<Employee[]>(`/employees${companyQuery}`, []),
      ])
    : [[], []] as [Campaign[], Employee[]];
  const leadCampaigns = campaigns.filter((campaign) => isLeadCampaign(campaign, employees));
  const details = Object.fromEntries(await Promise.all(leadCampaigns.map(async (campaign) => {
    const [outputs, review] = await Promise.all([
      serverApi<LeadOutputs>(`/campaigns/${campaign.id}/lead-outputs`, { outputs: [], rows: [], row_count: 0 }),
      serverApi<LeadReview>(`/campaigns/${campaign.id}/lead-review`, { items: [], counts: {}, eligible_count: 0 }),
    ]);
    return [campaign.id, { outputs, review }];
  })));
  const totalCurrent = leadCampaigns.reduce((sum, campaign) => sum + ((details as any)[campaign.id]?.review?.items?.length || 0), 0);
  const totalApproved = leadCampaigns.reduce((sum, campaign) => sum + Number((details as any)[campaign.id]?.review?.counts?.approved_for_outreach || 0), 0);
  const totalSendable = leadCampaigns.reduce((sum, campaign) => sum + Number((details as any)[campaign.id]?.review?.approval_eligible_count || 0), 0);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-zinc-500">Company &gt; Leads</p>
          <h1 className="text-2xl font-semibold">Lead Workspace</h1>
          <p className="text-sm text-zinc-400">Current unique lead pools, source evidence, quality status and approval controls before any sales outreach.</p>
        </div>
        <div className="text-sm text-zinc-400">{leadCampaigns.length} lead sources</div>
      </div>
      <CompanySelector companies={companies} selectedCompanyId={companyId} label="Company" />
      {!companyId ? <div className="card text-sm text-amber-300">Select a company to review leads.</div> : null}
      {companyId ? <div className="grid gap-3 md:grid-cols-4">
        <div className="card"><p className="text-sm text-zinc-400">Current unique leads</p><p className="mt-2 text-3xl font-semibold">{totalCurrent}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Approved now</p><p className="mt-2 text-3xl font-semibold">{totalApproved}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Sendable evidence</p><p className="mt-2 text-3xl font-semibold">{totalSendable}</p></div>
        <div className="card"><p className="text-sm text-zinc-400">Historical decisions</p><p className="mt-2 text-sm text-zinc-300">Tracked separately from current files</p></div>
      </div> : null}
      {leadCampaigns.map((campaign) => {
        const detail = (details as Record<string, { outputs: LeadOutputs; review: LeadReview }>)[campaign.id] || { outputs: { outputs: [], rows: [] }, review: { items: [], counts: {}, eligible_count: 0 } };
        const assumed = countBy(detail.review.items, 'email_confidence', 'assumed');
        const publicUnverified = countBy(detail.review.items, 'email_confidence', 'public_unverified');
        const verified = countBy(detail.review.items, 'email_confidence', 'verified');
        return (
          <section className="card" key={campaign.id} data-voryx-lead-workspace-campaign>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-sm text-zinc-500">Lead source</p>
                <h2 className="text-xl font-semibold">{campaign.name}</h2>
                <p className="text-sm text-zinc-400">{campaign.industry || 'Industry missing'} / {campaign.geographic_area || 'Location missing'} / {campaign.target_audience || 'Target customer missing'}</p>
              </div>
              <div className="text-right text-xs text-zinc-400">
                <div>Current file rows: <span className="text-zinc-100">{detail.review.items.length}</span></div>
                <div>Approved: <span className="text-zinc-100">{detail.review.counts.approved_for_outreach || 0}</span></div>
                <div>Source: <span className="text-zinc-100">{detail.review.source_path || 'none'}</span></div>
              </div>
            </div>
            <div className="mt-3 grid gap-2 md:grid-cols-3">
              <div className="rounded border border-zinc-800 p-2 text-xs"><div className="text-zinc-500">Verified/public evidence</div><div className="text-xl font-semibold">{verified + publicUnverified}</div></div>
              <div className="rounded border border-zinc-800 p-2 text-xs"><div className="text-zinc-500">Assumed or weak</div><div className="text-xl font-semibold">{assumed}</div></div>
              <div className="rounded border border-zinc-800 p-2 text-xs"><div className="text-zinc-500">Daily lead goal</div><div className="text-xl font-semibold">{campaign.daily_lead_goal || 0}</div></div>
            </div>
            <div className="mt-3 rounded border border-amber-900 bg-amber-950/20 p-2 text-xs text-amber-200">
              Only leads with verified or public source evidence can be approved for draft generation. Assumed addresses stay visible for review but remain blocked.
            </div>
            <LeadOutputsPanel outputs={detail.outputs.outputs || []} rows={detail.outputs.rows || []} />
            <OutreachControlsPanel companyId={campaign.company_id} campaignId={campaign.id} mode="lead_research" reportHref={`/reports${queryString({ company_id: campaign.company_id })}`} />
          </section>
        );
      })}
      {companyId && !leadCampaigns.length ? <div className="card text-sm text-zinc-400">No lead generation source exists for this company yet. Create a sales campaign with a lead generation source, or upload/import a real lead list.</div> : null}
    </div>
  );
}
