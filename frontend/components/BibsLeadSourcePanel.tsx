'use client';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Status = {
  configured?: boolean;
  status?: string;
  message?: string;
  blockers?: string[];
  path?: string;
  exists?: boolean;
  config?: Record<string, any>;
  latest_source_file?: string;
  existing_current_unique_emails?: number;
  prospect_emails_sent?: number;
};

const defaultForm = {
  source_type: 'uploaded_seed_csv',
  uploaded_csv_path: '',
  source_urls: '',
  search_queries: '',
  target_geography: 'Toronto',
  target_customer: 'independent cafe owners',
  exclusions: 'franchises, chains',
  lead_limit: 25,
  evidence_required: true,
  dedupe_against_previous_bibs: true,
};

export function BibsLeadSourcePanel({ companyId, leadCampaignId }: { companyId: string; leadCampaignId: string }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [form, setForm] = useState(defaultForm);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  async function load() {
    try {
      const data = await api(`/companies/${companyId}/bibs-lead-source-config`);
      setStatus(data);
      if (data.config) {
        setForm({
          ...defaultForm,
          ...data.config,
          source_urls: Array.isArray(data.config.source_urls) ? data.config.source_urls.join('\n') : (data.config.source_urls || ''),
          search_queries: Array.isArray(data.config.search_queries) ? data.config.search_queries.join('\n') : (data.config.search_queries || ''),
        });
      }
      setError('');
    } catch (err: any) {
      setError(err?.message || 'BIBS source config failed to load');
    }
  }

  useEffect(() => { if (companyId === 'company-brew-it-by-sash') load(); }, [companyId]);
  if (companyId !== 'company-brew-it-by-sash') return null;

  function update(key: string, value: any) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    setBusy('save'); setMessage(''); setError('');
    try {
      const result = await api(`/companies/${companyId}/bibs-lead-source-config`, { method: 'PUT', body: JSON.stringify(form) });
      setStatus(result);
      setMessage(result.message || 'Source config saved to Hermes workspace.');
      await load();
    } catch (err: any) { setError(err?.message || 'Source config save failed'); }
    finally { setBusy(''); }
  }

  async function testConfig() {
    setBusy('test'); setMessage(''); setError('');
    try {
      const result = await api(`/companies/${companyId}/bibs-lead-source-config/test`, { method: 'POST' });
      setStatus(result);
      setMessage(result.message || (result.ok ? 'Source config test passed.' : 'Source config needs attention.'));
    } catch (err: any) { setError(err?.message || 'Source config test failed'); }
    finally { setBusy(''); }
  }

  async function runLeadGeneration() {
    setBusy('run'); setMessage(''); setError('');
    try {
      const result = await api(`/campaigns/${leadCampaignId}/sales/find-leads`, { method: 'POST' });
      setMessage(result.message || 'Lead generation finished. Check Jobs and Lead Workspace for evidence.');
      await load();
    } catch (err: any) { setError(err?.message || 'Lead generation failed'); }
    finally { setBusy(''); }
  }

  return (
    <section className="rounded border border-amber-900 bg-amber-950/10 p-3" data-voryx-bibs-source-config>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-amber-100">BIBS Lead Source Setup</h3>
          <p className="text-xs text-amber-200">Lead generation is active, but it needs a real source before Hermes can create new unique leads.</p>
        </div>
        <div className="text-xs text-zinc-400">Hermes path: {status?.path || '/opt/data/home/leads/bibs_real_lead_source_config.json'}</div>
      </div>
      {status?.message ? <div className="mt-2 rounded border border-zinc-800 p-2 text-xs text-zinc-300">{status.message}</div> : null}
      {status?.blockers?.length ? <div className="mt-2 rounded border border-red-900 bg-red-950/30 p-2 text-xs text-red-200">Blockers: {status.blockers.join(', ')}</div> : null}
      {error ? <div className="mt-2 rounded border border-red-900 bg-red-950/40 p-2 text-xs text-red-200">{error}</div> : null}
      {message ? <div className="mt-2 rounded border border-emerald-900 bg-emerald-950/30 p-2 text-xs text-emerald-200">{message}</div> : null}
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        <label className="grid gap-1 text-xs text-zinc-300">Source type
          <select className="input" value={form.source_type} onChange={(e) => update('source_type', e.target.value)}>
            <option value="uploaded_seed_csv">Uploaded seed CSV</option>
            <option value="manual_import_csv">Manual import CSV</option>
            <option value="source_urls">Source URLs</option>
            <option value="search_queries">Search queries</option>
            <option value="existing_lead_pool">Existing lead pool</option>
          </select>
        </label>
        <label className="grid gap-1 text-xs text-zinc-300">Uploaded CSV path
          <input className="input" value={form.uploaded_csv_path} onChange={(e) => update('uploaded_csv_path', e.target.value)} placeholder="/opt/data/home/leads/source.csv" />
        </label>
        <label className="grid gap-1 text-xs text-zinc-300">Source URLs
          <textarea className="input min-h-24" value={form.source_urls} onChange={(e) => update('source_urls', e.target.value)} placeholder="One URL per line" />
        </label>
        <label className="grid gap-1 text-xs text-zinc-300">Search queries / source query
          <textarea className="input min-h-24" value={form.search_queries} onChange={(e) => update('search_queries', e.target.value)} placeholder="Toronto independent cafes contact pages" />
        </label>
        <input className="input" value={form.target_geography} onChange={(e) => update('target_geography', e.target.value)} placeholder="Target geography" />
        <input className="input" value={form.target_customer} onChange={(e) => update('target_customer', e.target.value)} placeholder="Target customer" />
        <input className="input" value={form.exclusions} onChange={(e) => update('exclusions', e.target.value)} placeholder="Exclusions" />
        <input className="input" type="number" min="1" max="250" value={form.lead_limit} onChange={(e) => update('lead_limit', Number(e.target.value || 25))} placeholder="Lead limit" />
      </div>
      <div className="mt-3 flex flex-wrap gap-3 text-xs text-zinc-300">
        <label><input type="checkbox" checked={form.evidence_required} onChange={(e) => update('evidence_required', e.target.checked)} /> Evidence required</label>
        <label><input type="checkbox" checked={form.dedupe_against_previous_bibs} onChange={(e) => update('dedupe_against_previous_bibs', e.target.checked)} /> Dedupe against previous BIBS leads</label>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'save'} onClick={save}>Save source config</button>
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'test'} onClick={testConfig}>Test source config</button>
        <button className="btn text-xs" type="button" disabled={busy === 'run'} onClick={runLeadGeneration}>Run lead generation now</button>
      </div>
      <div className="mt-2 grid gap-1 text-xs text-zinc-500">
        <div>Latest source file: {status?.latest_source_file || '-'}</div>
        <div>Existing current unique emails: {status?.existing_current_unique_emails ?? '-'}</div>
        <div>Prospect emails sent by this panel: {status?.prospect_emails_sent ?? 0}</div>
      </div>
    </section>
  );
}
