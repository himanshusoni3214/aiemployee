'use client';

import { useMemo, useState } from 'react';
import { api, downloadApi } from '../lib/api';
import { LocalTime } from './LocalTime';

type ScriptVersion = {
  id: string;
  version_number: number;
  status: string;
  name: string;
  opening_internal: string;
  opening_consented: string;
  purpose_statement: string;
  discovery_content: Record<string, string>;
  objection_library: Array<Record<string, any>>;
  closing_library: Record<string, string>;
  voicemail_content: string;
  voice_settings: Record<string, any>;
  compliance_content: Record<string, any>;
  talking_points: string[];
  estimated_prompt_tokens: number;
  node_changes: Array<Record<string, any>>;
  test_result: Record<string, any>;
  change_summary?: string | null;
  created_at?: string | null;
  published_at?: string | null;
  retell_agent_version?: number | null;
  retell_flow_version?: number | null;
  publish_state?: Record<string, any>;
  failure_stage?: string | null;
  recovery_action?: string | null;
};

type Studio = {
  published_version: ScriptVersion;
  current_draft?: ScriptVersion | null;
  live_retell_preview?: Record<string, any>;
  draft_preview?: ScriptVersion | null;
  versions: ScriptVersion[];
  compliance_items: Array<Record<string, any>>;
  compliance_blockers: string[];
  compliance_blocker_details?: Array<Record<string, any>>;
  compliance_packages?: Array<Record<string, any>>;
  automatic_system_checks?: Array<Record<string, any>>;
  consent_source_profiles?: Array<Record<string, any>>;
  consented_leads: Array<Record<string, any>>;
  eligible_lead_count: number;
  pilot_queue: Array<Record<string, any>>;
  pilot_settings: Record<string, any>;
  cost_projection: Record<string, any>;
  prospect_calling_globally_enabled: boolean;
  automatic_queue_enabled: boolean;
};

const TABS = ['Script', 'Objections', 'Compliance', 'Consented leads', 'Pilot', 'Versions'] as const;
const INPUT_CLASS = 'input w-full';

function Notice({ tone, children }: { tone: 'ok' | 'error' | 'info'; children: React.ReactNode }) {
  const style = tone === 'ok'
    ? 'border-emerald-800 bg-emerald-950/30 text-emerald-200'
    : tone === 'error'
      ? 'border-red-800 bg-red-950/30 text-red-200'
      : 'border-zinc-700 bg-zinc-900 text-zinc-300';
  return <div className={`rounded border p-3 text-sm ${style}`}>{children}</div>;
}

function Field({ label, value, onChange, limit = 1000, rows = 3 }: { label: string; value: string; onChange: (value: string) => void; limit?: number; rows?: number }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="flex justify-between text-zinc-300"><span>{label}</span><span className="text-xs text-zinc-500">{value.length}/{limit}</span></span>
      <textarea className={INPUT_CLASS} rows={rows} maxLength={limit} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export function CallScriptStudio({ studio, refresh }: { studio?: Studio; refresh: () => Promise<void> }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Script');
  const selected = useMemo(() => studio?.current_draft || studio?.versions.find((item) => ['draft', 'testing', 'approved', 'failed', 'failed_recoverable'].includes(item.status)) || studio?.published_version, [studio]);
  const [draft, setDraft] = useState<ScriptVersion | null>(null);
  const active = draft?.id === selected?.id ? draft : selected;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [lockedProposal, setLockedProposal] = useState('');
  const [complianceReason, setComplianceReason] = useState('');
  const [showIncomplete, setShowIncomplete] = useState(true);
  const [selectedProfileId, setSelectedProfileId] = useState('');
  const [csvPreview, setCsvPreview] = useState<Record<string, any> | null>(null);
  const [profileForm, setProfileForm] = useState<Record<string, any>>({
    name: '',
    approved_consent_language: '',
    organization_authorized: false,
    automated_call_permission: false,
    consent_proof_method: '',
    default_province: 'Ontario',
    default_timezone: 'America/Toronto',
    source_approval_evidence: '',
    approval_date: '',
    expires_at: '',
  });
  const [leadForm, setLeadForm] = useState<Record<string, any>>({
    first_name: '',
    last_name: '',
    phone_number: '',
    timezone: 'America/Toronto',
    province: 'Ontario',
    product_interest: 'Auto insurance',
    consent_status: 'under_review',
    consent_type: 'express_automated_call',
    consent_source: '',
    consent_text: '',
    consent_timestamp: '',
    consented_number: '',
    automated_or_synthesized_call_consent: false,
    organization_authorized: false,
    consent_proof: '',
    consent_withdrawn: false,
  });

  if (!studio || !active) return null;

  function mutate(update: Partial<ScriptVersion>) {
    setDraft({ ...active, ...update });
  }

  function updateProfile(update: Record<string, unknown>) {
    setProfileForm((current) => ({ ...current, ...update }));
  }

  async function action(fn: () => Promise<any>, success: string) {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await fn();
      setMessage(success);
      setDraft(null);
      await refresh();
    } catch (err: any) {
      setError(err?.message || 'Request failed');
    } finally {
      setBusy(false);
    }
  }

  async function ensureDraft() {
    if (active.status !== 'published') return active;
    const result = await api('/calling/allstate/script-versions/draft', { method: 'POST', body: JSON.stringify({ source_version: active.version_number }) });
    return result.script as ScriptVersion;
  }

  async function saveDraft() {
    const target = await ensureDraft();
    const source = draft || active;
    return api(`/calling/allstate/script-versions/${target.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        name: source.name,
        opening_internal: source.opening_internal,
        opening_consented: source.opening_consented,
        purpose_statement: source.purpose_statement,
        discovery_content: source.discovery_content,
        objection_library: source.objection_library,
        closing_library: source.closing_library,
        voicemail_content: source.voicemail_content,
        voice_settings: source.voice_settings,
        talking_points: source.talking_points,
        change_summary: source.change_summary || 'Script Studio edit',
      }),
    });
  }

  async function saveAndTest() {
    const target = await ensureDraft();
    const source = draft || active;
    return api(`/calling/allstate/script-versions/${target.id}/save-and-test`, {
      method: 'POST',
      body: JSON.stringify({
        name: source.name,
        opening_internal: source.opening_internal,
        opening_consented: source.opening_consented,
        purpose_statement: source.purpose_statement,
        discovery_content: source.discovery_content,
        objection_library: source.objection_library,
        closing_library: source.closing_library,
        voicemail_content: source.voicemail_content,
        voice_settings: source.voice_settings,
        talking_points: source.talking_points,
        change_summary: source.change_summary || 'Script Studio edit',
      }),
    });
  }

  async function proposeLockedChange() {
    if (!lockedProposal.trim()) throw new Error('Describe the proposed locked-section change');
    const target = await ensureDraft();
    return api(`/calling/allstate/script-versions/${target.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        compliance_content: { ...target.compliance_content, proposed_change: lockedProposal.trim() },
        change_summary: `Locked-section proposal: ${lockedProposal.trim()}`,
      }),
    });
  }

  async function previewCsv(file?: File) {
    if (!file) return;
    if (!selectedProfileId) {
      setError('Select a Consent Source Profile before uploading a simple CSV.');
      return;
    }
    const form = new FormData();
    form.append('profile_id', selectedProfileId);
    form.append('file', file);
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await api('/calling/allstate/consented-leads/preview-csv', { method: 'POST', body: form });
      setCsvPreview(result.preview);
      setMessage('CSV preview complete. No rows were imported.');
    } catch (err: any) {
      setError(err?.message || 'CSV preview failed');
    } finally {
      setBusy(false);
    }
  }

  const test = active.test_result || {};
  const projection = studio.cost_projection || {};

  return (
    <section className="card" data-call-script-studio>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs text-zinc-500">Sales Workforce &gt; Allstate - Himanshu &gt; Allstate Quote Appointment Calling &gt; Channels &gt; Calling</p>
          <h2 className="mt-1 text-xl font-semibold">Script Studio</h2>
          <p className="text-sm text-zinc-400">Versioned sales language, compliance evidence and individually approved consented-lead pilot controls.</p>
        </div>
        <div className="text-right text-sm">
          <div>Live: v{studio.published_version.version_number}</div>
          <div className="text-zinc-500">Retell agent {studio.live_retell_preview?.agent_version ?? '-'} / flow {studio.live_retell_preview?.flow_version ?? '-'}</div>
          <div className="text-zinc-500">Live {projection.live_model} / post-call {projection.post_call_model}</div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-1 border-b border-zinc-800" role="tablist">
        {TABS.map((item) => (
          <button key={item} type="button" role="tab" aria-selected={tab === item} className={`px-3 py-2 text-sm ${tab === item ? 'border-b-2 border-emerald-500 text-emerald-200' : 'text-zinc-400'}`} onClick={() => setTab(item)}>{item}</button>
        ))}
      </div>

      {message ? <div className="mt-4"><Notice tone="ok">{message}</Notice></div> : null}
      {error ? <div className="mt-4"><Notice tone="error">{error}</Notice></div> : null}
      {active.status === 'failed' || active.status === 'failed_recoverable' ? (
        <div className="mt-4"><Notice tone="error">Your changes are saved but are not live. Production is still using v{studio.published_version.version_number}. Failure stage: {active.failure_stage || 'provider publish'}. {active.recovery_action || 'Retry publish or return to editing.'}</Notice></div>
      ) : null}

      {tab === 'Script' ? (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded border border-emerald-900 bg-emerald-950/20 p-3 text-sm" data-live-retell-card>
              <div className="text-xs font-semibold text-emerald-300">LIVE IN RETELL</div>
              <div className="mt-2">Voryx v{studio.published_version.version_number} / Retell agent {studio.live_retell_preview?.agent_version ?? '-'} / flow {studio.live_retell_preview?.flow_version ?? '-'}</div>
              <div className="mt-1 text-xs text-zinc-500">Published <LocalTime value={studio.published_version.published_at} /></div>
              <p className="mt-2 text-zinc-300">{studio.live_retell_preview?.opening_consented || studio.published_version.opening_consented}</p>
              <div className={`mt-2 text-xs ${studio.live_retell_preview?.node_text_verified ? 'text-emerald-300' : 'text-amber-300'}`}>{studio.live_retell_preview?.node_text_verified ? 'Exact Retell node text verified' : studio.live_retell_preview?.verification_error || 'Retell text verification pending'}</div>
            </div>
            <div className="rounded border border-zinc-700 p-3 text-sm" data-current-draft-card>
              <div className="text-xs font-semibold text-zinc-300">CURRENT DRAFT</div>
              <div className="mt-1 text-xs text-zinc-500">DRAFT PREVIEW</div>
              <div className="mt-2">Voryx v{active.version_number} / {active.status}</div>
              <div className="mt-1 text-xs text-zinc-500">{active.node_changes?.length || 0} changed fields / {active.test_result?.passed ? 'tests passed' : 'not tested'}</div>
              <p className="mt-2 text-zinc-300">{active.opening_consented}</p>
              {active.id !== studio.published_version.id ? <div className="mt-2 text-xs text-amber-300">Not live until Retell publish and exact verification complete.</div> : <div className="mt-2 text-xs text-emerald-300">This version is live.</div>}
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Estimated prompt</div>{active.estimated_prompt_tokens} tokens</div>
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Maximum call</div>4 minutes / no retry / concurrency 1</div>
          </div>
          <Field label="Internal-test opening" value={active.opening_internal} onChange={(value) => mutate({ opening_internal: value })} />
          <Field label="Consented-lead opening" value={active.opening_consented} onChange={(value) => mutate({ opening_consented: value })} />
          <Field label="Reason for call" value={active.purpose_statement} onChange={(value) => mutate({ purpose_statement: value })} />
          <div className="grid gap-3 md:grid-cols-2">
            <Field label="Product-interest question" value={active.discovery_content?.product_interest || ''} onChange={(value) => mutate({ discovery_content: { ...active.discovery_content, product_interest: value } })} />
            <Field label="Coverage-review question" value={active.discovery_content?.coverage_review || ''} onChange={(value) => mutate({ discovery_content: { ...active.discovery_content, coverage_review: value } })} />
            <Field label="Appointment close" value={active.closing_library?.appointment || ''} onChange={(value) => mutate({ closing_library: { ...active.closing_library, appointment: value } })} />
            <Field label="Renewal callback close" value={active.closing_library?.renewal_callback || ''} onChange={(value) => mutate({ closing_library: { ...active.closing_library, renewal_callback: value } })} />
            <Field label="Busy callback close" value={active.closing_library?.busy_callback || ''} onChange={(value) => mutate({ closing_library: { ...active.closing_library, busy_callback: value } })} />
            <Field label="Voicemail" value={active.voicemail_content || ''} onChange={(value) => mutate({ voicemail_content: value })} />
          </div>
          <Field label="Voice and delivery notes" value={String(active.voice_settings?.tone_notes || '')} onChange={(value) => mutate({ voice_settings: { ...active.voice_settings, tone_notes: value } })} />
          <label className="block space-y-1 text-sm"><span>Approved campaign talking points, one per line</span><textarea className={INPUT_CLASS} rows={3} value={(active.talking_points || []).join('\n')} onChange={(event) => mutate({ talking_points: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })} /></label>
          <details className="rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Dynamic-variable preview</summary>
            <div className="mt-3 grid gap-2 text-xs md:grid-cols-2 xl:grid-cols-5">
              {['customer_name', 'agent_name', 'product_interest', 'consent_source', 'consent_date', 'renewal_month', 'slot_one', 'slot_two', 'callback_date', 'callback_time'].map((name) => <div key={name} className="rounded border border-zinc-900 p-2"><span className="font-mono text-zinc-500">{`{{${name}}}`}</span><div className="mt-1">Sample {name.replaceAll('_', ' ')}</div></div>)}
            </div>
          </details>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn" disabled={busy} onClick={() => void action(saveAndTest, 'Draft saved, tested and approved for publish in one step.')}>Save and test</button>
            <button type="button" className="btn-secondary" disabled={busy || !test.passed || active.status === 'published'} onClick={() => void action(() => api(`/calling/allstate/script-versions/${active.id}/publish`, { method: 'POST' }), 'Published and verified against the existing Retell agent, flow and number.')}>{active.status === 'failed' || active.status === 'failed_recoverable' ? 'Retry publish' : 'Publish'}</button>
            {(active.status === 'failed' || active.status === 'failed_recoverable') ? <>
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => setDraft({ ...active, status: 'draft' })}>Return to editing</button>
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => void action(() => api(`/calling/allstate/script-versions/${active.id}/discard`, { method: 'POST' }), 'Failed draft discarded. Live production was not changed.')}>Discard failed draft</button>
            </> : null}
          </div>
          <details className="rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Advanced actions</summary>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => void action(saveDraft, 'Draft saved. Retell production was not changed.')}>Save draft only</button>
              <button type="button" className="btn-secondary" disabled={busy || active.status === 'published'} onClick={() => void action(() => api(`/calling/allstate/script-versions/${active.id}/test`, { method: 'POST' }), 'All 15 deterministic draft scenarios were evaluated.')}>Test only</button>
              <button type="button" className="btn-secondary" disabled={busy || !test.passed} onClick={() => void action(() => api(`/calling/allstate/script-versions/${active.id}/request-approval`, { method: 'POST' }), 'Script approved for publishing.')}>Request approval only</button>
            </div>
          </details>
          {test.required_scenarios_total ? <Notice tone={test.passed ? 'ok' : 'error'}>{test.required_scenarios_passed}/{test.required_scenarios_total} scenarios passed. Sales score {test.sales_score}/10. Missing variables: {(test.missing_dynamic_variables || []).join(', ') || 'none'}. Missing tools: {(test.missing_retell_tools || []).join(', ') || 'none'}.</Notice> : null}
          {active.node_changes?.length ? (
            <details className="rounded border border-zinc-800 p-3">
              <summary className="cursor-pointer text-sm font-semibold">Compare mapped Retell changes</summary>
              <div className="mt-3 space-y-2">{active.node_changes.map((change, index) => <div key={`${change.field}-${index}`} className="rounded border border-zinc-900 p-2 text-xs"><div className="font-medium">{change.field} → {change.retell_node}</div><div className="text-zinc-500">Token difference {change.estimated_token_difference}; publish required {String(change.retell_publish_required)}</div></div>)}</div>
            </details>
          ) : null}
        </div>
      ) : null}

      {tab === 'Objections' ? (
        <div className="mt-4 space-y-3">
          <Notice tone="info">Maximum one reframe after a soft or first neutral objection. A second refusal, hard rejection or DNC ends the call.</Notice>
          {(active.objection_library || []).map((item, index) => (
            <div className="grid gap-2 border-b border-zinc-800 pb-3 md:grid-cols-[180px_110px_1fr_180px]" key={item.key}>
              <div><div className="font-medium">{item.name}</div><div className="text-xs text-zinc-500">{(item.example_phrases || []).join(' / ')}</div></div>
              <div className="text-sm"><div>{item.classification}</div><div className="text-xs text-zinc-500">Max {item.maximum_attempts}</div></div>
              <textarea className={INPUT_CLASS} rows={2} value={item.response || ''} disabled={['hard', 'DNC'].includes(item.classification)} onChange={(event) => {
                const objection_library = [...active.objection_library];
                objection_library[index] = { ...item, response: event.target.value };
                mutate({ objection_library });
              }} />
              <div className="text-xs text-zinc-400"><div>Node: {item.destination_node}</div><div>{item.compliance_status}</div><div>{item.active ? 'Active' : 'Inactive'}</div></div>
            </div>
          ))}
          <button type="button" className="btn" disabled={busy} onClick={() => void action(saveDraft, 'Objection library saved as a draft.')}>Save objection draft</button>
        </div>
      ) : null}

      {tab === 'Compliance' ? (
        <div className="mt-4 space-y-4">
          <Notice tone={studio.compliance_blockers.length ? 'error' : 'ok'}>
            {studio.compliance_blockers.length ? <div>
              <div className="font-semibold">{studio.compliance_blockers.length} mandatory compliance items remain incomplete. Prospect calls are blocked.</div>
              <ol className="mt-2 list-decimal space-y-1 pl-5">
                {(studio.compliance_blocker_details || []).map((item) => <li key={item.item_key}><span className="font-medium">{item.label}</span> — Missing: {(item.missing_fields || []).join(', ')}</li>)}
              </ol>
            </div> : 'All mandatory evidence is current.'}
          </Notice>
          <div className="grid gap-3 lg:grid-cols-2">
            {(studio.compliance_packages || []).map((item) => <CompliancePackage key={item.package_key} item={item} busy={busy} run={action} />)}
          </div>
          <div className="rounded border border-zinc-800 p-3">
            <div className="font-semibold">Automatic Voryx system checks</div>
            <div className="mt-2 grid gap-2 text-sm md:grid-cols-2">
              {(studio.automatic_system_checks || []).map((item) => <div key={item.key} className="flex items-center justify-between border-b border-zinc-900 py-1"><span>{item.label}</span><span className={item.passed ? 'text-emerald-300' : 'text-red-300'}>{item.passed ? 'Verified' : 'Blocked'}</span></div>)}
            </div>
          </div>
          <div className="rounded border border-zinc-800 p-3">
            <div className="flex items-center gap-2 font-semibold"><span aria-hidden="true">🔒</span> Compliance-locked script rules</div>
            <div className="mt-2 grid gap-2 text-sm md:grid-cols-2">{Object.entries(active.compliance_content || {}).filter(([key]) => key !== 'proposed_change').map(([key, value]) => <div key={key} className="flex justify-between border-b border-zinc-900 py-1"><span>{key.replaceAll('_', ' ')}</span><span>{String(value)}</span></div>)}</div>
            <div className="mt-3 grid gap-2 md:grid-cols-[1fr_auto]"><input className={INPUT_CLASS} placeholder="Describe a locked-section change proposal" value={lockedProposal} onChange={(event) => setLockedProposal(event.target.value)} /><button className="btn-secondary" type="button" disabled={busy} onClick={() => void action(proposeLockedChange, 'Locked change recorded as a draft. Separate compliance approval is required.')}>Propose change</button></div>
            {active.compliance_content?.proposed_change ? <div className="mt-3 grid gap-2 md:grid-cols-[1fr_1fr_auto]"><div className="rounded border border-amber-800 p-2 text-sm text-amber-200">{active.compliance_content.proposed_change}</div><input className={INPUT_CLASS} placeholder="Separate compliance approval reason" value={complianceReason} onChange={(event) => setComplianceReason(event.target.value)} /><button type="button" className="btn-secondary" disabled={busy || !complianceReason.trim()} onClick={() => void action(() => api(`/calling/allstate/script-versions/${active.id}/compliance-approval`, { method: 'POST', body: JSON.stringify({ reason: complianceReason }) }), 'Locked-section change separately approved and audited.')}>Approve locked change</button></div> : null}
          </div>
          <details className="rounded border border-zinc-800 p-3" open>
            <summary className="cursor-pointer font-semibold">Detailed 19-record audit</summary>
            <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={showIncomplete} onChange={(event) => setShowIncomplete(event.target.checked)} /> Show only incomplete</label>
            <div className="mt-3 table-wrap">
              <table className="ops-table"><thead><tr><th>Requirement</th><th>Status</th><th>Approver</th><th>Evidence</th><th>Effective</th><th>Action</th></tr></thead>
                <tbody>{studio.compliance_items.filter((item) => !showIncomplete || item.status !== 'approved').map((item) => <ComplianceRow key={item.item_key} item={item} busy={busy} run={action} />)}</tbody>
              </table>
            </div>
          </details>
        </div>
      ) : null}

      {tab === 'Consented leads' ? (
        <div className="mt-4 space-y-4">
          <Notice tone="info">Consent is never inferred. Exact-number automated-call consent, documentary proof, DNCL, internal DNC, suppression, approved script and recipient-local calling hours are all required.</Notice>
          <div className="rounded border border-zinc-800 p-3">
            <div className="font-semibold">Consent Source Profile</div>
            <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <label className="space-y-1 text-sm"><span>Selected profile</span><select className={INPUT_CLASS} value={selectedProfileId} onChange={(event) => setSelectedProfileId(event.target.value)}><option value="">Select profile</option>{(studio.consent_source_profiles || []).map((profile) => <option value={profile.id} key={profile.id}>{profile.name}</option>)}</select></label>
              <label className="space-y-1 text-sm"><span>Source name</span><input className={INPUT_CLASS} value={profileForm.name} onChange={(event) => updateProfile({ name: event.target.value })} /></label>
              <label className="space-y-1 text-sm"><span>Consent proof method</span><input className={INPUT_CLASS} value={profileForm.consent_proof_method} onChange={(event) => updateProfile({ consent_proof_method: event.target.value })} /></label>
              <label className="space-y-1 text-sm md:col-span-2 xl:col-span-3"><span>Exact approved consent language</span><textarea className={INPUT_CLASS} rows={3} value={profileForm.approved_consent_language} onChange={(event) => updateProfile({ approved_consent_language: event.target.value })} /></label>
              <label className="space-y-1 text-sm"><span>Source approval evidence</span><input className={INPUT_CLASS} value={profileForm.source_approval_evidence} onChange={(event) => updateProfile({ source_approval_evidence: event.target.value })} /></label>
              <label className="space-y-1 text-sm"><span>Approval date</span><input className={INPUT_CLASS} type="datetime-local" value={profileForm.approval_date} onInput={(event) => updateProfile({ approval_date: event.currentTarget.value })} onChange={(event) => updateProfile({ approval_date: event.target.value })} /></label>
              <label className="space-y-1 text-sm"><span>Expiry, if applicable</span><input className={INPUT_CLASS} type="datetime-local" value={profileForm.expires_at} onInput={(event) => updateProfile({ expires_at: event.currentTarget.value })} onChange={(event) => updateProfile({ expires_at: event.target.value })} /></label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={profileForm.organization_authorized} onChange={(event) => updateProfile({ organization_authorized: event.target.checked })} /> Organization authorized</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={profileForm.automated_call_permission} onChange={(event) => updateProfile({ automated_call_permission: event.target.checked })} /> Automated/synthesized-call permission</label>
            </div>
            <button type="button" className="btn-secondary mt-3" disabled={busy} onClick={() => void action(async () => {
              const result = await api('/calling/allstate/consent-source-profiles', { method: 'POST', body: JSON.stringify(profileForm) });
              setSelectedProfileId(result.profile.id);
              return result;
            }, 'Consent Source Profile saved and selected.')}>Save Consent Source Profile</button>
          </div>
          <div className="rounded border border-zinc-800 p-3">
            <div className="font-semibold">CSV import</div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" className="btn-secondary" onClick={() => void action(() => downloadApi('/calling/allstate/consented-leads/template.csv?mode=simple', 'allstate-consented-leads-simple.csv'), 'Simple CSV template downloaded.')}>Download simple CSV template</button>
              <button type="button" className="btn-secondary" onClick={() => void action(() => downloadApi('/calling/allstate/consented-leads/template.csv?mode=advanced', 'allstate-consented-leads-advanced.csv'), 'Advanced CSV template downloaded.')}>Download advanced CSV template</button>
              <label className="btn-secondary cursor-pointer">Preview simple CSV<input className="sr-only" type="file" accept=".csv,text/csv" onChange={(event) => void previewCsv(event.target.files?.[0])} /></label>
            </div>
            <div className="mt-2 text-xs text-zinc-500">Simple required columns: first_name, phone_number, consent_timestamp, consent_reference. Imported consent remains under review.</div>
            {csvPreview ? <div className="mt-3 space-y-3">
              <div className="grid gap-2 text-sm md:grid-cols-4"><div>Total {csvPreview.total_rows}</div><div className="text-emerald-300">Valid {csvPreview.valid_rows}</div><div className="text-amber-300">Needs review {csvPreview.rows_needing_review}</div><div>Duplicates {csvPreview.duplicate_numbers}</div></div>
              <div className="table-wrap"><table className="ops-table"><thead><tr><th>Row</th><th>Normalized phone</th><th>Result</th><th>Blocked reason</th></tr></thead><tbody>{(csvPreview.rows || []).map((row: Record<string, any>) => <tr key={row.row}><td>{row.row}</td><td>{row.normalized_phone}</td><td>{row.valid ? 'Valid' : 'Review'}</td><td>{(row.reasons || []).join('; ') || '-'}</td></tr>)}</tbody></table></div>
              <button type="button" className="btn" disabled={busy || !csvPreview.valid_rows} onClick={() => void action(() => api('/calling/allstate/consented-leads/import', { method: 'POST', body: JSON.stringify({ rows: csvPreview.import_rows }) }), 'Valid rows imported under review. Invalid rows were not imported.')}>Import valid rows</button>
            </div> : null}
          </div>
          <details className="rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer font-semibold">Advanced manual lead entry</summary>
            <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {['first_name', 'last_name', 'phone_number', 'timezone', 'province', 'product_interest', 'consent_status', 'consent_type', 'consent_source', 'consent_timestamp', 'consented_number', 'consent_proof'].map((key) => <label className="space-y-1 text-sm" key={key}><span>{key.replaceAll('_', ' ')}</span><input className={INPUT_CLASS} value={leadForm[key] || ''} onChange={(event) => setLeadForm({ ...leadForm, [key]: event.target.value })} /></label>)}
              <label className="space-y-1 text-sm md:col-span-2 xl:col-span-3"><span>Exact consent text</span><textarea className={INPUT_CLASS} rows={3} value={leadForm.consent_text || ''} onChange={(event) => setLeadForm({ ...leadForm, consent_text: event.target.value })} /></label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={leadForm.automated_or_synthesized_call_consent} onChange={(event) => setLeadForm({ ...leadForm, automated_or_synthesized_call_consent: event.target.checked })} /> Express automated/synthesized-call consent</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={leadForm.organization_authorized} onChange={(event) => setLeadForm({ ...leadForm, organization_authorized: event.target.checked })} /> Identified organization authorized</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={leadForm.consent_withdrawn} onChange={(event) => setLeadForm({ ...leadForm, consent_withdrawn: event.target.checked })} /> Consent withdrawn</label>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" className="btn" disabled={busy} onClick={() => void action(() => api('/calling/allstate/consented-leads/import', { method: 'POST', body: JSON.stringify(leadForm) }), 'Lead imported and eligibility evaluated.')}>Add manual lead</button>
            </div>
          </details>
          <div className="text-sm">Fully eligible uploaded leads: <strong>{studio.eligible_lead_count}</strong></div>
          <div className="table-wrap"><table className="ops-table"><thead><tr><th>Lead</th><th>Phone</th><th>Consent</th><th>DNCL/DNC</th><th>Eligibility</th><th>Action</th></tr></thead><tbody>
            {studio.consented_leads.map((lead) => <tr key={lead.id}><td>{lead.first_name} {lead.last_name}</td><td>{lead.phone_number_masked}<div className="text-xs text-zinc-500">{lead.timezone}</div></td><td>{lead.consent_status}<div className="text-xs text-zinc-500">{lead.consent_source}</div></td><td>{lead.dncl_status} / {lead.internal_dnc_clear && lead.suppression_clear ? 'clear' : 'review'}</td><td><div>{lead.eligibility_status}</div>{lead.eligibility_reasons?.length ? <div className="max-w-md text-xs text-amber-300">{lead.eligibility_reasons.join('; ')}</div> : null}</td><td><button type="button" className="btn-secondary text-xs" disabled={busy || lead.eligibility_status !== 'Ready for pilot'} onClick={() => void action(() => api(`/calling/allstate/consented-leads/${lead.id}/approve-pilot`, { method: 'POST' }), 'Lead individually approved for the pilot queue.')}>Approve for pilot</button></td></tr>)}
            {!studio.consented_leads.length ? <tr><td colSpan={6} className="text-zinc-400">No consented calling leads uploaded.</td></tr> : null}
          </tbody></table></div>
        </div>
      ) : null}

      {tab === 'Pilot' ? (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Pilot cap</div>5 leads / 5 calls daily</div>
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Execution</div>One at a time / no retries</div>
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Queue</div>Batch and schedule OFF</div>
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Global prospect calling</div>OFF</div>
          </div>
          <Notice tone="info">Each eligible lead needs individual approval and the exact phrase <span className="font-mono">{studio.pilot_settings.confirmation_required}</span>. No call is launched automatically.</Notice>
          <div className="grid gap-3 md:grid-cols-3">
            {[5, 20, 100].map((count) => <div className="rounded border border-zinc-800 p-3 text-sm" key={count}><div className="text-zinc-500">{count} calls projected</div>${studio.cost_projection?.projected_cost_usd?.[String(count)] ?? '-'} USD</div>)}
          </div>
          <div className="table-wrap"><table className="ops-table"><thead><tr><th>Lead</th><th>Consent</th><th>Local time</th><th>Script / agent</th><th>Status</th><th>Control</th></tr></thead><tbody>
            {studio.pilot_queue.map((item) => <PilotRow key={item.id} item={item} busy={busy} run={action} />)}
            {!studio.pilot_queue.length ? <tr><td colSpan={6} className="text-zinc-400">No individually approved pilot leads.</td></tr> : null}
          </tbody></table></div>
        </div>
      ) : null}

      {tab === 'Versions' ? (
        <div className="mt-4 table-wrap">
          <table className="ops-table"><thead><tr><th>Version</th><th>Status</th><th>Summary</th><th>Tests</th><th>Retell</th><th>Created</th><th>Action</th></tr></thead><tbody>
            {studio.versions.map((version) => <tr key={version.id}><td>v{version.version_number}</td><td>{version.status}</td><td>{version.change_summary || '-'}</td><td>{version.test_result?.passed ? `${version.test_result.sales_score}/10` : '-'}</td><td>agent {version.retell_agent_version ?? '-'} / flow {version.retell_flow_version ?? '-'}</td><td><LocalTime value={version.created_at} /></td><td>{version.status === 'archived' ? <button type="button" className="btn-secondary text-xs" disabled={busy} onClick={() => void action(() => api(`/calling/allstate/script-versions/${version.version_number}/rollback`, { method: 'POST', body: JSON.stringify({ reason: `Rollback to approved version ${version.version_number}` }) }), `Version ${version.version_number} republished to the same Retell flow.`)}>Rollback</button> : '-'}</td></tr>)}
          </tbody></table>
        </div>
      ) : null}
    </section>
  );
}

function ComplianceRow({ item, busy, run }: { item: Record<string, any>; busy: boolean; run: (fn: () => Promise<any>, success: string) => Promise<void> }) {
  const [status, setStatus] = useState(item.status || 'incomplete');
  const [approver, setApprover] = useState(item.approver || '');
  const [evidence, setEvidence] = useState(item.evidence || '');
  const [effectiveAt, setEffectiveAt] = useState(item.effective_at ? String(item.effective_at).slice(0, 16) : '');
  return <tr><td>{item.label}<div className="text-xs text-zinc-500">{item.category}</div></td><td><select className={INPUT_CLASS} value={status} onChange={(event) => setStatus(event.target.value)}><option>incomplete</option><option>under_review</option><option>approved</option><option>rejected</option><option>expired</option></select></td><td><input className={INPUT_CLASS} value={approver} onChange={(event) => setApprover(event.target.value)} /></td><td><input className={INPUT_CLASS} value={evidence} onChange={(event) => setEvidence(event.target.value)} /></td><td><input className={INPUT_CLASS} type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} /></td><td><button type="button" className="btn-secondary text-xs" disabled={busy} onClick={() => void run(() => api(`/calling/allstate/compliance/${item.item_key}`, { method: 'PATCH', body: JSON.stringify({ status, approver, evidence, effective_at: effectiveAt || null }) }), `${item.label} updated.`)}>Save</button></td></tr>;
}

function CompliancePackage({ item, busy, run }: { item: Record<string, any>; busy: boolean; run: (fn: () => Promise<any>, success: string) => Promise<void> }) {
  const existing = (item.items || []).find((entry: Record<string, any>) => entry.evidence || entry.approver || entry.effective_at) || {};
  const [approver, setApprover] = useState(existing.approver || '');
  const [evidence, setEvidence] = useState(existing.evidence || '');
  const [effectiveAt, setEffectiveAt] = useState(existing.effective_at ? String(existing.effective_at).slice(0, 16) : '');
  return <div className="rounded border border-zinc-800 p-3 text-sm">
    <div className="flex items-start justify-between gap-2"><div className="font-semibold">{item.label}</div><div className={item.approved_count === item.total_count ? 'text-emerald-300' : 'text-amber-300'}>{item.approved_count}/{item.total_count}</div></div>
    <div className="mt-2 text-xs text-zinc-500">{(item.items || []).map((entry: Record<string, any>) => entry.label).join(' · ')}</div>
    {item.external_evidence_required ? <div className="mt-3 space-y-2">
      <input className={INPUT_CLASS} placeholder="Approver" value={approver} onChange={(event) => setApprover(event.target.value)} />
      <input className={INPUT_CLASS} placeholder="Approval evidence" value={evidence} onChange={(event) => setEvidence(event.target.value)} />
      <input className={INPUT_CLASS} type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} />
      <button type="button" className="btn-secondary" disabled={busy || !approver || !evidence || !effectiveAt} onClick={() => void run(() => api(`/calling/allstate/compliance-packages/${item.package_key}`, { method: 'POST', body: JSON.stringify({ approver, evidence, effective_at: effectiveAt }) }), `${item.label} saved across its underlying audit records.`)}>Save approval package</button>
    </div> : <div className="mt-3 text-xs text-zinc-400">{item.package_key === 'system' ? 'Verified automatically from the running system.' : 'Evaluated separately for each uploaded lead.'}</div>}
  </div>;
}

function PilotRow({ item, busy, run }: { item: Record<string, any>; busy: boolean; run: (fn: () => Promise<any>, success: string) => Promise<void> }) {
  const [confirmation, setConfirmation] = useState('');
  return <tr><td>{item.lead_name}<div className="text-xs text-zinc-500">{item.phone_number_masked}</div></td><td>{item.consent_status}<div className="text-xs text-zinc-500">{item.consent_source}</div></td><td>{item.recipient_local_time}<div className={item.inside_calling_window ? 'text-xs text-emerald-300' : 'text-xs text-amber-300'}>{item.inside_calling_window ? 'Inside window' : 'Outside window'}</div></td><td>v{item.script_version}<div className="max-w-48 truncate font-mono text-xs text-zinc-500">{item.retell_agent_id} / {item.retell_agent_version ?? '-'}</div></td><td>{item.status}<div className="text-xs text-zinc-500">Max ${item.estimated_max_cost_usd} USD</div></td><td><input className={INPUT_CLASS} placeholder={item.confirmation_required} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /><button type="button" className="btn mt-2 text-xs" disabled={busy || confirmation !== item.confirmation_required || item.status !== 'approved'} onClick={() => void run(() => api(`/calling/allstate/pilot/${item.id}/place`, { method: 'POST', body: JSON.stringify({ confirmation_text: confirmation }) }), 'Approved consented lead call was requested.')}>Place approved call</button></td></tr>;
}
