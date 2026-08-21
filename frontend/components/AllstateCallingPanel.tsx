'use client';

import { useEffect, useMemo, useState } from 'react';
import { api, downloadApi } from '../lib/api';
import { LocalTime } from './LocalTime';
import { CallScriptStudio } from './CallScriptStudio';

type Tab = 'overview' | 'contacts' | 'calling' | 'results' | 'script' | 'settings';

type Readiness = {
  ready: boolean;
  checks: Array<{ code: string; label: string; ready: boolean }>;
  blockers: Array<{ code: string; label: string }>;
  eligible_contacts: number;
  calling_now: boolean;
  next_calling_window?: string | null;
  daily_limit: number;
  concurrency: number;
  confirmation_required: string;
};

type ImportReview = {
  id: string;
  filename: string;
  uploaded: number;
  ready: number;
  needs_review: number;
  blocked: number;
  reason_counts: Record<string, number>;
  created_at?: string;
  rows: Array<{
    id: string;
    row_number: number;
    first_name?: string | null;
    phone_number_masked?: string | null;
    classification: string;
    blocker_messages: string[];
  }>;
};

type QueueItem = {
  id: string;
  name: string;
  phone_number_masked: string;
  status: string;
  outcome?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  duration_seconds?: number | null;
  cost_usd?: number | null;
  summary?: string | null;
  transcript?: string | null;
  recording_url?: string | null;
  sales_score?: number | null;
  objections?: unknown[];
  disposition?: string | null;
  renewal_month?: string | null;
  callback_at?: string | null;
  callback_timezone?: string | null;
  callback_reason?: string | null;
  appointment?: boolean;
  failure_code?: string | null;
  error_message?: string | null;
  advanced?: Record<string, unknown>;
};

type CallingState = {
  status: string;
  progress: { completed: number; total: number; queued: number; calling: number };
  today: { attempts: number; answered: number; appointments: number; callbacks: number; dnc: number; no_answer: number };
  cost_today: number;
  average_cost: number;
  callbacks: Array<{ id: string; callback_at?: string | null; timezone?: string | null; reason?: string | null; status: string }>;
  queue_items: QueueItem[];
};

export type CallingWorkspace = {
  confirmation_required?: string;
  baseline?: string;
  settings: {
    from_number?: string | null;
    campaign_status?: string;
    daily_call_limit?: number;
    concurrent_call_limit?: number;
    prospect_calling_enabled?: boolean;
    automated_queue_enabled?: boolean;
    baseline_version?: string;
  };
  health: {
    api_authenticated?: boolean;
    agent_exists?: boolean;
    agent_name?: string | null;
    agent_id?: string | null;
    agent_version?: number | string | null;
    number_exists?: boolean;
    outbound_agent_correctly_assigned?: boolean;
    webhook_signature_key_configured?: boolean;
    tool_token_configured?: boolean;
    response_engine?: { type?: string; conversation_flow_id?: string; version?: number | null } | null;
    blockers?: string[];
  };
  readiness?: Readiness;
  latest_import?: ImportReview | null;
  calling?: CallingState;
  attempts?: any[];
  script_studio?: any;
  preview?: any;
  warnings?: string[];
  agent_migration?: any;
};

const tabs: Array<{ id: Tab; label: string }> = [
  { id: 'overview', label: 'Overview' },
  { id: 'contacts', label: 'Contacts' },
  { id: 'calling', label: 'Calling' },
  { id: 'results', label: 'Results' },
  { id: 'script', label: 'Script' },
  { id: 'settings', label: 'Settings' },
];

const sourceOptions = [
  { value: 'allstate_web', label: 'Allstate approved web leads' },
  { value: 'customer_callback', label: 'Customer callback requests' },
  { value: 'existing_customer', label: 'Existing customer inquiries' },
  { value: 'documented_referral', label: 'Referrals with documented permission' },
  { value: 'other', label: 'Other' },
];

const organizationOptions = [
  { value: 'Allstate', label: 'Allstate' },
  { value: 'Himanshu Soni, Allstate Sales Agent', label: 'Himanshu Soni, Allstate Sales Agent' },
  { value: 'other', label: 'Other' },
];

const consentWordingOptions = [
  {
    value: 'web_form',
    label: 'Web form - automated call consent',
    wording: 'I agree to receive automated or synthesized calls from Himanshu Soni, an Allstate Sales Agent, at the phone number I provided about insurance products or services.',
  },
  {
    value: 'callback_request',
    label: 'Customer requested an automated callback',
    wording: 'I request and consent to an automated or synthesized callback from Himanshu Soni, an Allstate Sales Agent, at the phone number I provided about insurance products or services.',
  },
  {
    value: 'recorded_verbal',
    label: 'Recorded verbal consent',
    wording: 'I consent to receive automated or synthesized calls from Himanshu Soni, an Allstate Sales Agent, at this phone number about insurance products or services.',
  },
  { value: 'other', label: 'Other - enter exact wording', wording: '' },
];

const proofOptions = [
  { value: 'web_form', label: 'Web form submission', stored: 'Web form submission linked by consent reference' },
  { value: 'crm_record', label: 'CRM consent record', stored: 'CRM consent record linked by consent reference' },
  { value: 'recorded_call', label: 'Recorded phone consent', stored: 'Recorded phone consent linked by consent reference' },
  { value: 'signed_form', label: 'Signed form', stored: 'Signed consent form linked by consent reference' },
  { value: 'other', label: 'Other', stored: '' },
];

const evidenceOptions = [
  { value: 'consent_reference', label: 'Consent reference in uploaded CSV', stored: 'Per-lead consent evidence is linked by consent_reference in the uploaded CSV' },
  { value: 'crm_record', label: 'CRM record ID in consent reference', stored: 'Per-lead CRM record ID is supplied in consent_reference' },
  { value: 'web_submission', label: 'Web submission ID in consent reference', stored: 'Per-lead web submission ID is supplied in consent_reference' },
  { value: 'other', label: 'Other', stored: '' },
];

function money(value?: number | null) {
  return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(value || 0);
}

function statusLabel(value?: string | null) {
  return String(value || 'not_started').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="rounded border border-zinc-800 p-3"><div className="text-xs text-zinc-500">{label}</div><div className="mt-1 text-xl font-semibold">{value}</div></div>;
}

function Message({ value, error = false }: { value: string; error?: boolean }) {
  if (!value) return null;
  return <div role="status" className={`rounded border p-3 text-sm ${error ? 'border-red-800 bg-red-950/30 text-red-200' : 'border-emerald-800 bg-emerald-950/30 text-emerald-200'}`}>{value}</div>;
}

export function AllstateCallingPanel({ initialWorkspace }: { initialWorkspace: CallingWorkspace }) {
  const [workspace, setWorkspace] = useState(initialWorkspace);
  const [tab, setTab] = useState<Tab>('overview');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [profileId, setProfileId] = useState(initialWorkspace.script_studio?.consent_source_profiles?.[0]?.id || '');
  const [batchConsentConfirmed, setBatchConsentConfirmed] = useState(false);
  const [dryRun, setDryRun] = useState<any>(null);
  const [showStart, setShowStart] = useState(false);
  const [selectedResult, setSelectedResult] = useState<QueueItem | null>(null);
  const [dailyLimit, setDailyLimit] = useState(initialWorkspace.settings?.daily_call_limit || 20);
  const [concurrency, setConcurrency] = useState(initialWorkspace.settings?.concurrent_call_limit || 1);
  const [sourcePreset, setSourcePreset] = useState('');
  const [organizationPreset, setOrganizationPreset] = useState('Allstate');
  const [consentWordingPreset, setConsentWordingPreset] = useState('');
  const [proofPreset, setProofPreset] = useState('');
  const [evidencePreset, setEvidencePreset] = useState('consent_reference');
  const [hasExpiry, setHasExpiry] = useState(false);
  const [profile, setProfile] = useState({
    name: '', organization_represented: 'Allstate', approved_consent_language: '',
    organization_authorized: false, automated_call_permission: false,
    consent_proof_method: '', source_approval_evidence: evidenceOptions[0].stored, approval_date: '',
    expires_at: '', default_province: 'Ontario', default_timezone: 'America/Toronto',
  });

  async function refresh() {
    const next = await api('/calling/allstate');
    setWorkspace(next);
    if (!profileId && next.script_studio?.consent_source_profiles?.[0]?.id) setProfileId(next.script_studio.consent_source_profiles[0].id);
  }

  useEffect(() => {
    const interval = window.setInterval(() => void refresh().catch((reason) => console.warn('Calling refresh failed', reason)), 10000);
    return () => window.clearInterval(interval);
  }, []);

  async function perform(action: () => Promise<any>, success: string) {
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await action();
      setMessage(success);
      await refresh();
      return result;
    } catch (reason: any) {
      console.error('Calling action failed', reason);
      setError(reason?.message || 'Action failed');
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function upload() {
    if (!file || !profileId || !batchConsentConfirmed) { setError('Select a Consent Source, choose a CSV file, and confirm consent for the uploaded numbers.'); return; }
    const body = new FormData();
    body.append('profile_id', profileId);
    body.append('batch_consent_attested', 'true');
    body.append('file', file);
    await perform(() => api('/calling/allstate/contacts/upload', { method: 'POST', body }), 'Contacts uploaded and validated.');
  }

  async function runDryRun() {
    const result = await perform(() => api('/calling/allstate/dry-run', { method: 'POST' }), 'Dry run completed. No calls were placed.');
    if (result) setDryRun(result.dry_run);
  }

  async function campaignAction(action: 'pause' | 'resume' | 'stop') {
    await perform(() => api(`/calling/allstate/campaign/${action}`, { method: 'POST' }), `Campaign ${action} succeeded.`);
  }

  async function startCampaign() {
    const result = await perform(() => api('/calling/allstate/campaign/start', {
      method: 'POST', body: JSON.stringify({ confirmation: workspace.readiness?.confirmation_required || 'START APPROVED CALLING CAMPAIGN' }),
    }), 'Approved calling campaign started.');
    if (result) setShowStart(false);
  }

  async function saveLimits() {
    await perform(() => api('/calling/allstate/campaign/settings', {
      method: 'PATCH', body: JSON.stringify({ daily_call_limit: dailyLimit, concurrent_call_limit: concurrency }),
    }), 'Calling limits saved.');
  }

  async function saveProfile() {
    const approvalDate = profile.approval_date || new Date().toISOString();
    if (!profile.name || !profile.organization_represented || !profile.approved_consent_language || !profile.consent_proof_method || !profile.source_approval_evidence) {
      setError('Select the lead source, consent wording, proof method and evidence location.');
      return;
    }
    if (!profile.organization_authorized || !profile.automated_call_permission) {
      setError('Confirm organization authorization and automated-call permission.');
      return;
    }
    const result = await perform(() => api('/calling/allstate/consent-source-profiles', {
      method: 'POST', body: JSON.stringify({ ...profile, approval_date: approvalDate, expires_at: hasExpiry ? profile.expires_at : '' }),
    }), 'Consent Source saved.');
    if (result?.profile?.id) setProfileId(result.profile.id);
  }

  function chooseSource(value: string) {
    setSourcePreset(value);
    const selected = sourceOptions.find((item) => item.value === value);
    setProfile((current) => ({ ...current, name: value === 'other' ? '' : selected?.label || '' }));
  }

  function chooseOrganization(value: string) {
    setOrganizationPreset(value);
    setProfile((current) => ({ ...current, organization_represented: value === 'other' ? '' : value }));
  }

  function chooseConsentWording(value: string) {
    setConsentWordingPreset(value);
    const selected = consentWordingOptions.find((item) => item.value === value);
    setProfile((current) => ({ ...current, approved_consent_language: selected?.wording || '' }));
  }

  function chooseProof(value: string) {
    setProofPreset(value);
    const selected = proofOptions.find((item) => item.value === value);
    setProfile((current) => ({ ...current, consent_proof_method: selected?.stored || '' }));
  }

  function chooseEvidence(value: string) {
    setEvidencePreset(value);
    const selected = evidenceOptions.find((item) => item.value === value);
    setProfile((current) => ({ ...current, source_approval_evidence: selected?.stored || '' }));
  }

  const calling: CallingState = workspace.calling || { status: 'not_started', progress: { completed: 0, total: 0, queued: 0, calling: 0 }, today: { attempts: 0, answered: 0, appointments: 0, callbacks: 0, dnc: 0, no_answer: 0 }, queue_items: [], callbacks: [], cost_today: 0, average_cost: 0 };
  const readiness: Readiness = workspace.readiness || { ready: false, checks: [], blockers: [{ code: 'workspace', label: 'Calling workspace' }], eligible_contacts: 0, calling_now: false, daily_limit: 20, concurrency: 1, confirmation_required: 'START APPROVED CALLING CAMPAIGN' };
  const campaignStatus = calling.status || workspace.settings?.campaign_status || 'not_started';
  const sourceProfiles = workspace.script_studio?.consent_source_profiles || [];
  const published = workspace.script_studio?.published_version;
  const blockedReasonRows = useMemo(() => Object.entries(workspace.latest_import?.reason_counts || {}).sort((a: any, b: any) => b[1] - a[1]), [workspace.latest_import]);
  const missingConsentEvidence = Boolean(
    workspace.latest_import?.reason_counts?.CONSENT_REFERENCE_MISSING
    || workspace.latest_import?.reason_counts?.CONSENT_TIMESTAMP_MISSING,
  );
  const startBlockerMessage = missingConsentEvidence
    ? 'Start Calling is blocked: confirm that every uploaded number has prior express consent, then upload the file again.'
    : `Start Calling is blocked: ${readiness.blockers.map((item) => item.label).join(', ') || 'calling readiness is incomplete'}.`;

  return (
    <div className="space-y-4" data-voryx-calling-workspace>
      <section className="border-b border-zinc-800 pb-4">
        <p className="text-sm text-zinc-500">Allstate / Calling</p>
        <div className="mt-1 flex flex-wrap items-center justify-between gap-3">
          <div><h1 className="text-2xl font-semibold">AI Calling</h1><p className="text-sm text-zinc-400">Upload approved contacts, validate them, and run the campaign.</p></div>
          <span className={`text-sm font-medium ${campaignStatus === 'running' ? 'text-emerald-300' : 'text-zinc-300'}`}>{statusLabel(campaignStatus)}</span>
        </div>
      </section>

      <nav className="flex gap-1 overflow-x-auto border-b border-zinc-800" aria-label="Calling sections">
        {tabs.map((item) => <button key={item.id} type="button" className={`border-b-2 px-3 py-2 text-sm ${tab === item.id ? 'border-emerald-500 text-zinc-100' : 'border-transparent text-zinc-400'}`} onClick={() => setTab(item.id)}>{item.label}</button>)}
      </nav>

      <Message value={message} /><Message value={error} error />

      {tab === 'overview' ? <>
        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <Metric label="Calling provider" value={workspace.health?.api_authenticated ? 'Connected' : 'Blocked'} />
          <Metric label="Phone number" value="+1 437-747-5010" />
          <Metric label="Script" value={published?.version_number === 8 ? 'Ready' : 'Blocked'} />
          <Metric label="Compliance" value={(workspace.script_studio?.compliance_blockers || []).length ? 'Blocked' : 'Ready'} />
          <Metric label="Contacts ready" value={readiness.eligible_contacts} />
          <Metric label="Calls today" value={calling.today.attempts || 0} />
          <Metric label="Appointments" value={calling.today.appointments || 0} />
          <Metric label="Callbacks" value={calling.today.callbacks || 0} />
        </section>
        <section className="card" data-voryx-readiness>
          <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">{readiness.ready ? 'READY TO CALL' : 'NOT READY'}</h2><p className="text-sm text-zinc-400">Only approved contacts that pass every control can enter the queue.</p></div><button type="button" className="btn-secondary" onClick={() => setTab(readiness.blockers[0]?.code === 'consent_source' ? 'settings' : 'contacts')}>{readiness.ready ? 'View contacts' : `Fix ${readiness.blockers[0]?.label || 'readiness'}`}</button></div>
          <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">{(readiness.ready ? readiness.checks : readiness.checks.filter((check) => !check.ready)).map((check) => <div key={check.code} className="flex items-center justify-between border-b border-zinc-800 py-2 text-sm"><span>{check.label}</span><span className={check.ready ? 'text-emerald-300' : 'text-amber-300'}>{check.ready ? 'Ready' : 'Blocked'}</span></div>)}</div>
          <div className="mt-4 grid gap-3 md:grid-cols-3"><Metric label="Daily limit" value={readiness.daily_limit} /><Metric label="Concurrency" value={readiness.concurrency} /><Metric label="Calling now" value={readiness.calling_now ? 'YES' : 'NO'} /></div>
          {!readiness.calling_now && readiness.next_calling_window ? <p className="mt-3 text-sm text-zinc-400">Next calling window: <LocalTime value={readiness.next_calling_window} /></p> : null}
        </section>
      </> : null}

      {tab === 'contacts' ? <>
        <section className="card" data-voryx-contact-upload>
          <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-semibold">Contacts</h2><p className="text-sm text-zinc-400">Required: first name and Canadian phone number.</p></div><button type="button" className="btn-secondary" onClick={() => void downloadApi('/calling/allstate/consented-leads/template.csv?mode=simple', 'allstate-calling-contacts.csv')}>Download Template</button></div>
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_auto]">
            <label className="text-sm">Consent Source<select data-voryx-consent-source className="input mt-1" value={profileId} onChange={(event) => setProfileId(event.target.value)}><option value="">Select source</option>{sourceProfiles.map((item: any) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label className="text-sm">CSV file<input className="input mt-1" type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] || null)} /></label>
            <button type="button" className="btn self-end" disabled={busy || !file || !profileId || !batchConsentConfirmed} onClick={() => void upload()}>Upload Contacts</button>
            <label className="flex items-start gap-2 text-sm md:col-span-3">
              <input data-voryx-batch-consent type="checkbox" className="mt-1" checked={batchConsentConfirmed} onChange={(event) => setBatchConsentConfirmed(event.target.checked)} />
              <span>I confirm every number in this file gave prior express consent for automated or synthesized calls from the organization in the selected Consent Source, and the source record can be produced.</span>
            </label>
          </div>
        </section>
        {workspace.latest_import ? <section className="card" data-voryx-import-review>
          <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">Import review</h2><p className="text-sm text-zinc-400">{workspace.latest_import.filename} / <LocalTime value={workspace.latest_import.created_at} /></p></div><button type="button" className="btn-secondary" disabled={!workspace.latest_import.blocked && !workspace.latest_import.needs_review} onClick={() => void downloadApi(`/calling/allstate/contacts/imports/${workspace.latest_import?.id}/blocked.csv`, 'allstate-blocked-contacts.csv')}>Download blocked rows</button></div>
          <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4"><Metric label="Uploaded" value={workspace.latest_import.uploaded} /><Metric label="Ready for AI call" value={workspace.latest_import.ready} /><Metric label="Needs review" value={workspace.latest_import.needs_review} /><Metric label="Blocked" value={workspace.latest_import.blocked} /></div>
          {blockedReasonRows.length ? <div className="mt-4"><h3 className="text-sm font-semibold">Grouped reasons</h3><div className="mt-2 grid gap-2 md:grid-cols-2">{blockedReasonRows.map(([code, count]: any) => <div className="flex justify-between border-b border-zinc-800 py-2 text-sm" key={code}><span>{statusLabel(code)}</span><span>{count}</span></div>)}</div></div> : null}
          <div className="mt-4 flex flex-wrap gap-2"><button type="button" className="btn-secondary" disabled={busy} onClick={() => void runDryRun()}>DRY RUN MY CONTACTS</button>{readiness.ready ? <button type="button" className="btn" onClick={() => { setTab('calling'); setShowStart(true); }}>Start calling {readiness.eligible_contacts} eligible contacts</button> : null}</div>
          {!readiness.ready ? <p className="mt-3 text-sm text-amber-300" data-voryx-start-calling-blocker>{startBlockerMessage}</p> : null}
        </section> : <section className="card text-sm text-zinc-400">No contacts uploaded yet.</section>}
        {dryRun ? <section className="card border-emerald-900"><h2 className="text-lg font-semibold">Dry run result</h2><p className="text-sm text-emerald-300">No telephone activity occurred.</p><div className="mt-3 grid gap-3 md:grid-cols-3"><Metric label="Uploaded" value={dryRun.uploaded} /><Metric label="Would call" value={dryRun.would_call} /><Metric label="Would block" value={dryRun.would_block} /><Metric label="Would call today" value={dryRun.would_call_today} /><Metric label="First call" value={<LocalTime value={dryRun.first_call} />} /><Metric label="Estimated cost today" value={`${money(dryRun.estimated_cost_usd.low)}–${money(dryRun.estimated_cost_usd.high)}`} /></div></section> : null}
      </> : null}

      {tab === 'calling' ? <>
        <section className="card" data-voryx-campaign-controls>
          <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-semibold">Allstate Quote Appointment Calling</h2><p className="text-sm text-zinc-400">Status: {statusLabel(campaignStatus)}</p></div><div className="flex flex-wrap gap-2">
            {['not_started', 'stopped', 'completed'].includes(campaignStatus) ? <button type="button" className="btn" disabled={!readiness.ready || busy} title={!readiness.ready ? startBlockerMessage : undefined} onClick={() => setShowStart(true)}>START CALLING</button> : null}
            {['running', 'waiting_for_window'].includes(campaignStatus) ? <button type="button" className="btn-secondary" disabled={busy} onClick={() => void campaignAction('pause')}>Pause</button> : null}
            {campaignStatus === 'paused' ? <button type="button" className="btn" disabled={busy} onClick={() => void campaignAction('resume')}>Resume</button> : null}
            {['running', 'waiting_for_window', 'paused'].includes(campaignStatus) ? <button type="button" className="btn-secondary" disabled={busy} onClick={() => void campaignAction('stop')}>Stop</button> : null}
          </div></div>
          {!readiness.ready && ['not_started', 'stopped', 'completed'].includes(campaignStatus) ? <p className="mt-3 text-sm text-amber-300" data-voryx-start-calling-blocker>{startBlockerMessage}</p> : null}
          <div className="mt-4 grid gap-3 md:grid-cols-4"><Metric label="Progress" value={`${calling.progress.completed} / ${calling.progress.total}`} /><Metric label="Queued" value={calling.progress.queued} /><Metric label="Calling now" value={calling.progress.calling} /><Metric label="Cost today" value={money(calling.cost_today)} /></div>
          <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4"><Metric label="Attempts" value={calling.today.attempts || 0} /><Metric label="Answered" value={calling.today.answered || 0} /><Metric label="Appointments" value={calling.today.appointments || 0} /><Metric label="Callbacks" value={calling.today.callbacks || 0} /><Metric label="DNC" value={calling.today.dnc || 0} /><Metric label="No answer" value={calling.today.no_answer || 0} /><Metric label="Avg cost/call" value={money(calling.average_cost)} /></div>
          {campaignStatus === 'waiting_for_window' ? <p className="mt-4 text-sm text-amber-300">Waiting for the next recipient-local calling window. This is not a failure.</p> : null}
        </section>
        {showStart ? <section className="card border-emerald-800" role="dialog" aria-label="Start calling confirmation"><h2 className="text-lg font-semibold">Confirm approved calling campaign</h2><div className="mt-3 grid gap-2 text-sm md:grid-cols-2"><div>Ready contacts: {readiness.eligible_contacts}</div><div>Blocked contacts: {(workspace.latest_import?.blocked || 0) + (workspace.latest_import?.needs_review || 0)}</div><div>From number: +14377475010</div><div>Script: v8</div><div>Calling now: {readiness.calling_now ? 'Yes' : 'No, wait for window'}</div><div>Concurrency: {readiness.concurrency}</div><div>Daily limit: {readiness.daily_limit}</div><div>Maximum calls today: {Math.min(readiness.eligible_contacts, readiness.daily_limit)}</div></div><div className="mt-4 flex gap-2"><button type="button" className="btn" disabled={busy} onClick={() => void startCampaign()}>START APPROVED CALLING CAMPAIGN</button><button type="button" className="btn-secondary" onClick={() => setShowStart(false)}>Cancel</button></div></section> : null}
      </> : null}

      {tab === 'results' ? <section className="card" data-voryx-call-results>
        <div className="flex items-center justify-between"><div><h2 className="text-lg font-semibold">Results</h2><p className="text-sm text-zinc-400">Appointments, callbacks, DNC and call outcomes update automatically.</p></div><button type="button" className="btn-secondary" onClick={() => void refresh()}>Refresh</button></div>
        <div className="table-wrap mt-4"><table className="ops-table"><thead><tr><th>Name</th><th>Status</th><th>Call time</th><th>Duration</th><th>Outcome</th><th>Renewal</th><th>Callback</th><th>Appointment</th><th>Sales score</th><th>Cost</th><th>Details</th></tr></thead><tbody>{calling.queue_items.map((item) => <tr key={item.id}><td>{item.name}</td><td>{statusLabel(item.status)}</td><td><LocalTime value={item.started_at || item.created_at} /></td><td>{item.duration_seconds ? `${item.duration_seconds}s` : '-'}</td><td>{statusLabel(item.outcome || item.disposition)}</td><td>{item.renewal_month || '-'}</td><td><LocalTime value={item.callback_at} /></td><td>{item.appointment ? 'Booked' : '-'}</td><td>{item.sales_score ?? '-'}</td><td>{money(item.cost_usd)}</td><td><button type="button" className="btn-secondary text-xs" onClick={() => setSelectedResult(item)}>View</button></td></tr>)}{!calling.queue_items.length ? <tr><td colSpan={11} className="text-zinc-400">No campaign results yet.</td></tr> : null}</tbody></table></div>
        {selectedResult ? <div className="mt-4 rounded border border-zinc-800 p-4" role="dialog" aria-label="Result details"><div className="flex items-start justify-between"><div><h3 className="font-semibold">{selectedResult.name}</h3><p className="text-sm text-zinc-400">{statusLabel(selectedResult.outcome || selectedResult.status)}</p></div><button className="btn-secondary" type="button" onClick={() => setSelectedResult(null)}>Close</button></div><div className="mt-3 grid gap-3 md:grid-cols-3"><Metric label="Summary" value={selectedResult.summary || '-'} /><Metric label="Callback" value={selectedResult.callback_at ? <LocalTime value={selectedResult.callback_at} /> : '-'} /><Metric label="Appointment" value={selectedResult.appointment ? 'Booked' : '-'} /><Metric label="Sales quality" value={selectedResult.sales_score ?? '-'} /><Metric label="Cost" value={money(selectedResult.cost_usd)} /></div>{selectedResult.error_message ? <p className="mt-3 text-sm text-red-300">{selectedResult.error_message}</p> : null}{selectedResult.recording_url ? <a className="btn-secondary mt-3 inline-block" href={selectedResult.recording_url}>Recording</a> : null}<details className="mt-3 rounded border border-zinc-800 p-3"><summary className="cursor-pointer text-sm font-medium">Transcript</summary><pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs text-zinc-300">{selectedResult.transcript || 'No transcript stored.'}</pre></details><details className="mt-3 rounded border border-zinc-800 p-3"><summary className="cursor-pointer text-sm font-medium">Advanced provider details</summary><pre className="mt-3 overflow-auto text-xs text-zinc-400">{JSON.stringify(selectedResult.advanced || {}, null, 2)}</pre></details></div> : null}
      </section> : null}

      {tab === 'script' ? <section className="card"><h2 className="text-lg font-semibold">Script</h2><p className="mt-1 text-sm text-zinc-400">The approved Allstate conversation is live and ready.</p><div className="mt-4 max-w-sm"><Metric label="Script status" value={published ? 'Ready' : 'Blocked'} /></div><details className="mt-4 rounded border border-zinc-800 p-3"><summary className="cursor-pointer text-sm font-semibold">Advanced Script Studio</summary><div className="mt-4"><CallScriptStudio studio={workspace.script_studio} refresh={refresh} /></div></details></section> : null}

      {tab === 'settings' ? <>
        <section className="card"><h2 className="text-lg font-semibold">Calling limits</h2><p className="text-sm text-zinc-400">Start conservatively. Automatic retries remain off.</p><div className="mt-4 grid gap-3 md:grid-cols-3"><label className="text-sm">Daily call limit<input className="input mt-1" type="number" min={1} max={500} value={dailyLimit} onChange={(event) => setDailyLimit(Number(event.target.value))} /></label><label className="text-sm">Concurrency<select className="input mt-1" value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))}>{[1, 2, 3, 5].map((value) => <option key={value} value={value}>{value}</option>)}</select></label><button type="button" className="btn self-end" disabled={busy} onClick={() => void saveLimits()}>Save limits</button></div></section>
        <section className="card" data-voryx-consent-source-setup>
          <h2 className="text-lg font-semibold">Consent Source</h2>
          <p className="text-sm text-zinc-400">Set this once for each approved lead source.</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-sm">Lead source
              <select data-voryx-source-preset className="input mt-1" value={sourcePreset} onChange={(event) => chooseSource(event.target.value)}>
                <option value="">Select</option>
                {sourceOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            {sourcePreset === 'other' ? <label className="text-sm">Source name<input className="input mt-1" value={profile.name} onChange={(event) => setProfile({ ...profile, name: event.target.value })} /></label> : null}

            <label className="text-sm">Organization represented
              <select data-voryx-organization-preset className="input mt-1" value={organizationPreset} onChange={(event) => chooseOrganization(event.target.value)}>
                {organizationOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            {organizationPreset === 'other' ? <label className="text-sm">Organization name<input className="input mt-1" value={profile.organization_represented} onChange={(event) => setProfile({ ...profile, organization_represented: event.target.value })} /></label> : null}

            <label className="text-sm md:col-span-2">Consent wording used
              <select data-voryx-consent-wording-preset className="input mt-1" value={consentWordingPreset} onChange={(event) => chooseConsentWording(event.target.value)}>
                <option value="">Select</option>
                {consentWordingOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            {consentWordingPreset === 'other' ? <label className="text-sm md:col-span-2">Exact consent wording<textarea className="input mt-1 min-h-24" value={profile.approved_consent_language} onChange={(event) => setProfile({ ...profile, approved_consent_language: event.target.value })} /></label> : null}

            <label className="text-sm">Consent proof
              <select data-voryx-proof-preset className="input mt-1" value={proofPreset} onChange={(event) => chooseProof(event.target.value)}>
                <option value="">Select</option>
                {proofOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            {proofPreset === 'other' ? <label className="text-sm">Proof method<input className="input mt-1" value={profile.consent_proof_method} onChange={(event) => setProfile({ ...profile, consent_proof_method: event.target.value })} /></label> : null}

            <label className="text-sm">Evidence location
              <select data-voryx-evidence-preset className="input mt-1" value={evidencePreset} onChange={(event) => chooseEvidence(event.target.value)}>
                {evidenceOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
            {evidencePreset === 'other' ? <label className="text-sm">Evidence reference<input className="input mt-1" value={profile.source_approval_evidence} onChange={(event) => setProfile({ ...profile, source_approval_evidence: event.target.value })} /></label> : null}

            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={profile.organization_authorized} onChange={(event) => setProfile({ ...profile, organization_authorized: event.target.checked })} /> Organization is authorized</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={profile.automated_call_permission} onChange={(event) => setProfile({ ...profile, automated_call_permission: event.target.checked })} /> Automated/synthesized calls are permitted</label>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={hasExpiry} onChange={(event) => setHasExpiry(event.target.checked)} /> Consent source has an expiry date</label>
            {hasExpiry ? <label className="text-sm">Expiry date<input className="input mt-1" type="datetime-local" value={profile.expires_at} onChange={(event) => setProfile({ ...profile, expires_at: event.target.value })} /></label> : null}
          </div>
          <button type="button" className="btn mt-4" disabled={busy} onClick={() => void saveProfile()}>Save Consent Source</button>
          {sourceProfiles.length ? <div className="mt-4 text-sm text-zinc-400">Saved sources: {sourceProfiles.map((item: any) => item.name).join(', ')}</div> : null}
        </section>
        <details className="card"><summary className="cursor-pointer text-sm font-semibold">Advanced technical details</summary><pre className="mt-4 max-h-96 overflow-auto text-xs text-zinc-400">{JSON.stringify({ baseline: workspace.baseline, health: workspace.health, warnings: workspace.warnings, migration: workspace.agent_migration }, null, 2)}</pre></details>
      </> : null}
    </div>
  );
}
