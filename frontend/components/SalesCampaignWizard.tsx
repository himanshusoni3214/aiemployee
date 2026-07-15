'use client';
import { useState } from 'react';
import { api } from '../lib/api';

const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'];

type Company = { id: string; name: string };

export function SalesCampaignWizard({ companyId, companies }: { companyId: string; companies: Company[] }) {
  const company = companies.find((item) => item.id === companyId);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [form, setForm] = useState({
    name: '',
    product: '',
    offer: '',
    sales_goal: '',
    target_customer: '',
    geography: '',
    industry: '',
    exclusions: '',
    lead_source_type: 'ai_internet_research',
    lead_source_file: '',
    reference_websites: '',
    preferred_keywords: '',
    avoid_keywords: '',
    known_competitors: '',
    preferred_source_types: '',
    approval_level: 'approve_every_lead_and_draft',
    daily_lead_goal: 25,
    daily_email_limit: 5,
    allowed_sending_start: '09:00',
    allowed_sending_end: '17:00',
    timezone: 'America/Toronto',
    internal_test_recipient: 'himanshusoni3214@gmail.com',
    report_recipient: 'himanshusoni3214@gmail.com',
  });

  function update(key: string, value: string | number) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function createCampaign() {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      const description = [
        form.offer ? `Offer: ${form.offer}` : '',
        form.product ? `Product/service: ${form.product}` : '',
        form.sales_goal ? `Sales goal: ${form.sales_goal}` : '',
        form.exclusions ? `Exclusions: ${form.exclusions}` : '',
      ].filter(Boolean).join('\n');
      const provisioning_result = {
        campaign_blueprint: 'sales_outreach',
        approval_level: form.approval_level,
        channels: {
          email: 'enabled',
          calling: 'not_connected',
          sms_text: 'not_connected',
          social_outreach: 'not_connected',
          whatsapp: 'not_connected',
        },
        lead_source: {
          type: form.lead_source_type,
          file: form.lead_source_file,
          reference_websites: form.reference_websites.split('\n').map((item) => item.trim()).filter(Boolean),
          preferred_keywords: form.preferred_keywords,
          avoid_keywords: form.avoid_keywords,
          known_competitors: form.known_competitors,
          preferred_source_types: form.preferred_source_types.split('\n').map((item) => item.trim()).filter(Boolean),
        },
      };
      const result = await api('/campaigns', {
        method: 'POST',
        body: JSON.stringify({
          company_id: companyId,
          name: form.name,
          description,
          industry: form.industry || form.product,
          target_audience: form.target_customer,
          geographic_area: form.geography,
          daily_lead_goal: Number(form.daily_lead_goal || 25),
          daily_email_goal: Number(form.daily_email_limit || 5),
          daily_email_limit: Number(form.daily_email_limit || 5),
          campaign_type: 'sales_outreach',
          provisioning_state: 'Draft',
          provisioning_result,
          timezone: form.timezone,
          allowed_sending_days: weekdays,
          allowed_sending_hours: { start: form.allowed_sending_start, end: form.allowed_sending_end },
          internal_test_recipient: form.internal_test_recipient,
          report_recipient: form.report_recipient,
          lead_source_type: form.lead_source_type,
          lead_source_file: form.lead_source_file,
          reference_websites: form.reference_websites.split('\n').map((item) => item.trim()).filter(Boolean),
          preferred_keywords: form.preferred_keywords,
          avoid_keywords: form.avoid_keywords,
          known_competitors: form.known_competitors,
          preferred_source_types: form.preferred_source_types.split('\n').map((item) => item.trim()).filter(Boolean),
          dry_run_mode: true,
          status: 'Active',
        }),
      });
      setMessage(`Sales campaign created and default employees provisioned: ${result.name || form.name}`);
      window.setTimeout(() => window.location.reload(), 900);
    } catch (err: any) {
      setError(err?.message || 'Sales campaign creation failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" data-voryx-sales-campaign-wizard>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-zinc-500">Create Sales Campaign</p>
          <h2 className="text-xl font-semibold">B2B sales workspace setup</h2>
          <p className="text-sm text-zinc-400">Creates the campaign, lead source, email workflow employees, reporting, schedules and safe Hermes jobs in one flow.</p>
        </div>
        <div className="rounded border border-zinc-800 px-3 py-2 text-xs text-zinc-300">Company: {company?.name || companyId}</div>
      </div>
      {error ? <div className="mt-3 rounded border border-red-900 bg-red-950/40 p-2 text-xs text-red-200">{error}</div> : null}
      {message ? <div className="mt-3 rounded border border-emerald-900 bg-emerald-950/30 p-2 text-xs text-emerald-200">{message}</div> : null}
      <div className="mt-4 grid gap-4">
        <fieldset className="rounded border border-zinc-800 p-3">
          <legend className="px-1 text-sm font-semibold">1. Business objective</legend>
          <div className="grid gap-2 md:grid-cols-2">
            <input className="input" placeholder="Campaign name" value={form.name} onChange={(e) => update('name', e.target.value)} />
            <input className="input" placeholder="Product/service being sold" value={form.product} onChange={(e) => update('product', e.target.value)} />
            <input className="input" placeholder="Offer" value={form.offer} onChange={(e) => update('offer', e.target.value)} />
            <input className="input" placeholder="Sales goal" value={form.sales_goal} onChange={(e) => update('sales_goal', e.target.value)} />
            <input className="input" placeholder="Target customer" value={form.target_customer} onChange={(e) => update('target_customer', e.target.value)} />
            <input className="input" placeholder="Geography" value={form.geography} onChange={(e) => update('geography', e.target.value)} />
            <input className="input" placeholder="Industry / niche" value={form.industry} onChange={(e) => update('industry', e.target.value)} />
            <input className="input" placeholder="Exclusions" value={form.exclusions} onChange={(e) => update('exclusions', e.target.value)} />
          </div>
        </fieldset>
        <fieldset className="rounded border border-zinc-800 p-3">
          <legend className="px-1 text-sm font-semibold">2. Lead source</legend>
          <div className="grid gap-2 md:grid-cols-2">
            <select className="input" value={form.lead_source_type} onChange={(e) => update('lead_source_type', e.target.value)}>
              <option value="ai_internet_research">AI Internet Research - generate leads from internet</option>
              <option value="uploaded_seed_csv">Upload CSV</option>
              <option value="existing_lead_pool">Use existing company lead pool</option>
              <option value="another_campaign">Use leads from another campaign</option>
              <option value="source_urls">Manual source URLs</option>
              <option value="search_queries">Manual search queries</option>
              <option value="social_media_groups" disabled>Social media/groups - not connected</option>
              <option value="google_maps_directory" disabled>Google Maps/business directory - not connected</option>
            </select>
            {form.lead_source_type === 'uploaded_seed_csv' ? <input className="input" placeholder="Optional upload CSV path" value={form.lead_source_file} onChange={(e) => update('lead_source_file', e.target.value)} /> : null}
            <textarea className="input min-h-20" placeholder="Optional reference URLs, one per line" value={form.reference_websites} onChange={(e) => update('reference_websites', e.target.value)} />
            <textarea className="input min-h-20" placeholder="Optional preferred keywords" value={form.preferred_keywords} onChange={(e) => update('preferred_keywords', e.target.value)} />
            <textarea className="input min-h-20" placeholder="Optional avoid keywords" value={form.avoid_keywords} onChange={(e) => update('avoid_keywords', e.target.value)} />
            <textarea className="input min-h-20" placeholder="Optional known competitors" value={form.known_competitors} onChange={(e) => update('known_competitors', e.target.value)} />
            <textarea className="input min-h-20" placeholder="Optional preferred source types" value={form.preferred_source_types} onChange={(e) => update('preferred_source_types', e.target.value)} />
          </div>
          <p className="mt-2 text-xs text-amber-200">Normal campaigns use AI Internet Research from your product, target customer, geography, exclusions and lead goal. URLs and CSVs are optional advanced references. If no web/search provider is connected, Hermes reports internet_research_provider_not_configured instead of generating fake or repeated leads.</p>
        </fieldset>
        <fieldset className="rounded border border-zinc-800 p-3">
          <legend className="px-1 text-sm font-semibold">3. Outreach channels</legend>
          <div className="grid gap-2 md:grid-cols-5 text-xs">
            <div className="rounded border border-emerald-900 bg-emerald-950/20 p-2 text-emerald-200">Email outreach: connected</div>
            <div className="rounded border border-zinc-800 p-2 text-zinc-500">Calling: not connected</div>
            <div className="rounded border border-zinc-800 p-2 text-zinc-500">SMS/Text: not connected</div>
            <div className="rounded border border-zinc-800 p-2 text-zinc-500">Social outreach: not connected</div>
            <div className="rounded border border-zinc-800 p-2 text-zinc-500">WhatsApp: not connected</div>
          </div>
        </fieldset>
        <fieldset className="rounded border border-zinc-800 p-3">
          <legend className="px-1 text-sm font-semibold">4. Approval level</legend>
          <select className="input" value={form.approval_level} onChange={(e) => update('approval_level', e.target.value)}>
            <option value="approve_every_lead_and_draft">Approve every lead and every draft</option>
            <option value="approve_leads_bulk_approve_drafts">Approve leads, bulk approve drafts after preview</option>
            <option value="semi_auto_after_first_batch">Semi-auto after first successful batch</option>
            <option value="manual_only">Manual only</option>
          </select>
        </fieldset>
        <fieldset className="rounded border border-zinc-800 p-3">
          <legend className="px-1 text-sm font-semibold">5. Limits and schedule</legend>
          <div className="grid gap-2 md:grid-cols-3">
            <input className="input" type="number" min="1" placeholder="Daily lead goal" value={form.daily_lead_goal} onChange={(e) => update('daily_lead_goal', Number(e.target.value || 25))} />
            <input className="input" type="number" min="1" max="5" placeholder="Daily email limit" value={form.daily_email_limit} onChange={(e) => update('daily_email_limit', Number(e.target.value || 5))} />
            <input className="input" placeholder="Timezone" value={form.timezone} onChange={(e) => update('timezone', e.target.value)} />
            <input className="input" type="time" value={form.allowed_sending_start} onChange={(e) => update('allowed_sending_start', e.target.value)} />
            <input className="input" type="time" value={form.allowed_sending_end} onChange={(e) => update('allowed_sending_end', e.target.value)} />
            <input className="input" placeholder="Internal test recipient" value={form.internal_test_recipient} onChange={(e) => update('internal_test_recipient', e.target.value)} />
            <input className="input" placeholder="Report recipient" value={form.report_recipient} onChange={(e) => update('report_recipient', e.target.value)} />
          </div>
        </fieldset>
        <fieldset className="rounded border border-zinc-800 p-3">
          <legend className="px-1 text-sm font-semibold">6. Auto-provision default employees</legend>
          <div className="grid gap-2 md:grid-cols-4 text-xs text-zinc-300">
            <span>Lead Researcher</span><span>Lead Verifier</span><span>Email Draft Writer</span><span>Email Sender</span><span>Reply Monitor disabled</span><span>Follow-up Manager safety locked</span><span>Daily Reporter</span>
          </div>
        </fieldset>
        <button className="btn w-fit" type="button" disabled={busy || !form.name || !form.target_customer || !form.geography || !form.industry} onClick={createCampaign}>Create sales campaign workspace</button>
      </div>
    </section>
  );
}
