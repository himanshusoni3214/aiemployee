'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, downloadApi } from '../lib/api';
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
  content_hash?: string | null;
  tested_content_hash?: string | null;
  approved_content_hash?: string | null;
  published_content_hash?: string | null;
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
const PUBLISHABLE_FIELDS = [
  'opening_internal',
  'opening_consented',
  'purpose_statement',
  'discovery_content',
  'objection_library',
  'closing_library',
  'voicemail_content',
  'voice_settings',
  'talking_points',
  'compliance_content',
] as const;

type FormValues = Pick<ScriptVersion, (typeof PUBLISHABLE_FIELDS)[number]> & {
  name: string;
  change_summary?: string | null;
};

function formFromVersion(version?: ScriptVersion | null): FormValues {
  return {
    name: version?.name || '',
    opening_internal: version?.opening_internal || '',
    opening_consented: version?.opening_consented || '',
    purpose_statement: version?.purpose_statement || '',
    discovery_content: version?.discovery_content || {},
    objection_library: version?.objection_library || [],
    closing_library: version?.closing_library || {},
    voicemail_content: version?.voicemail_content || '',
    voice_settings: version?.voice_settings || {},
    talking_points: version?.talking_points || [],
    compliance_content: version?.compliance_content || {},
    change_summary: version?.change_summary || 'Script Studio edit',
  };
}

function stableValue(value: any): any {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableValue(value[key])]));
  }
  return value;
}

function stableString(value: any) {
  return JSON.stringify(stableValue(value));
}

async function contentHash(values: FormValues) {
  const publishable = Object.fromEntries(PUBLISHABLE_FIELDS.map((field) => [field, values[field]]));
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(stableString(publishable)));
  return Array.from(new Uint8Array(digest)).map((item) => item.toString(16).padStart(2, '0')).join('');
}

function validateForm(values: FormValues): Record<string, string[]> {
  const errors: Record<string, string[]> = {};
  const add = (field: string, message: string) => {
    errors[field] = [...(errors[field] || []), message];
  };
  for (const [field, label] of [
    ['opening_internal', 'Internal-test opening'],
    ['opening_consented', 'Consented-lead opening'],
  ] as const) {
    const value = values[field].trim();
    if (!value) {
      add(field, `${label} is blank.`);
      continue;
    }
    if (value.length > 1000) add(field, `${label} must be 1,000 characters or fewer.`);
    if ((value.match(/\?/g) || []).length > 1) add(field, `${label} must contain no more than one question.`);
    if ((value.match(/\)/g) || []).length > (value.match(/\(/g) || []).length) add(field, `${label} contains an extra closing parenthesis.`);
    const customerMatches = value.match(/\{\{\s*customer_name\s*\}\}/g) || [];
    if (customerMatches.length !== 1) {
      if (/(?<!\{)\{customer_name\}(?!\})/.test(value)) {
        add(field, 'Customer-name variable is malformed. Use {{customer_name}}, not {customer_name}.');
      } else {
        add(field, 'Customer name is required exactly once as {{customer_name}}.');
      }
    }
    const remainder = value.replace(/\{\{\s*[a-zA-Z0-9_]+\s*\}\}/g, '');
    if (remainder.includes('{') || remainder.includes('}')) {
      add(field, 'Use {{customer_name}} with two opening and two closing braces.');
    }
  }
  if (values.voice_settings?.opening_style === 'confirm_person_first') {
    for (const [field, label] of [
      ['confirmed_person_internal', 'Confirmed-person internal introduction'],
      ['confirmed_person_consented', 'Confirmed-person prospect introduction'],
    ] as const) {
      const value = String(values.voice_settings?.[field] || '').trim();
      const lower = value.toLowerCase();
      if ((value.match(/\{\{customer_name\}\}/g) || []).length !== 1) add(`voice_settings.${field}`, `${label} must use {{customer_name}} exactly once.`);
      if (!lower.includes('ava')) add(`voice_settings.${field}`, `${label} must identify Ava.`);
      if (!lower.includes('himanshu soni')) add(`voice_settings.${field}`, `${label} must identify Himanshu Soni.`);
      if (!lower.includes('allstate') || !lower.includes('sales agent')) add(`voice_settings.${field}`, `${label} must identify the Allstate Sales Agent role.`);
      if (!value.includes('?') || !['thirty seconds', 'quick conversation'].some((term) => lower.includes(term))) add(`voice_settings.${field}`, `${label} must ask permission for a short conversation.`);
    }
    if (!String(values.voice_settings?.wrong_person_response || '').trim()) add('voice_settings.wrong_person_response', 'Wrong-person response is required.');
  }
  if (!values.purpose_statement.trim()) add('purpose_statement', 'Reason for call is required.');
  if (!String(values.discovery_content?.product_interest || '').trim()) add('discovery_content.product_interest', 'Product-interest question is required.');
  if (!String(values.discovery_content?.coverage_review || '').trim()) add('discovery_content.coverage_review', 'Coverage-review question is required.');
  if (!String(values.closing_library?.appointment || '').trim()) add('closing_library.appointment', 'Appointment close is missing.');
  if (!String(values.closing_library?.renewal_callback || '').trim()) add('closing_library.renewal_callback', 'Renewal callback close is missing.');
  if (!String(values.closing_library?.busy_callback || '').trim()) add('closing_library.busy_callback', 'Busy callback close is missing.');
  return errors;
}

function Notice({ tone, children }: { tone: 'ok' | 'error' | 'info'; children: React.ReactNode }) {
  const style = tone === 'ok'
    ? 'border-emerald-800 bg-emerald-950/30 text-emerald-200'
    : tone === 'error'
      ? 'border-red-800 bg-red-950/30 text-red-200'
      : 'border-zinc-700 bg-zinc-900 text-zinc-300';
  return <div className={`rounded border p-3 text-sm ${style}`}>{children}</div>;
}

function Field({ id, label, value, onChange, errors = [], limit = 1000, rows = 3 }: { id: string; label: string; value: string; onChange: (value: string) => void; errors?: string[]; limit?: number; rows?: number }) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="flex justify-between text-zinc-300"><span>{label}</span><span className="text-xs text-zinc-500">{value.length}/{limit}</span></span>
      <textarea id={id} className={`${INPUT_CLASS} ${errors.length ? 'border-red-700' : ''}`} aria-invalid={errors.length ? 'true' : 'false'} aria-describedby={errors.length ? `${id}-errors` : undefined} rows={rows} maxLength={limit} value={value} onChange={(event) => onChange(event.target.value)} />
      {errors.length ? <div id={`${id}-errors`} className="space-y-1 text-xs text-red-300">{errors.map((item) => <div key={item}>{item}</div>)}</div> : null}
    </label>
  );
}

export function CallScriptStudio({ studio, refresh, onDirtyChange }: { studio?: Studio; refresh: () => Promise<void>; onDirtyChange?: (dirty: boolean) => void }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('Script');
  const basePublishedVersion = studio?.published_version;
  const serverDraftVersion = studio?.current_draft || studio?.versions.find((item) => ['draft', 'testing', 'approved', 'failed', 'failed_recoverable'].includes(item.status)) || null;
  const serverSource = serverDraftVersion || basePublishedVersion;
  const [formValues, setFormValues] = useState<FormValues>(() => formFromVersion(serverSource));
  const [formSourceId, setFormSourceId] = useState(serverSource?.id || '');
  const [currentContentHash, setCurrentContentHash] = useState('');
  const idempotencyKey = useRef(crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [publishingStep, setPublishingStep] = useState(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [apiFieldErrors, setApiFieldErrors] = useState<Record<string, string[]>>({});
  const [failureDetails, setFailureDetails] = useState<ApiError | null>(null);
  const [pendingTab, setPendingTab] = useState<(typeof TABS)[number] | null>(null);
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

  const baseForm = useMemo(() => formFromVersion(basePublishedVersion), [basePublishedVersion]);
  const serverForm = useMemo(() => formFromVersion(serverSource), [serverSource]);
  const changedFields = useMemo(
    () => PUBLISHABLE_FIELDS.filter((field) => stableString(formValues[field]) !== stableString(baseForm[field])),
    [formValues, baseForm],
  );
  const isDirty = useMemo(() => stableString(formValues) !== stableString(serverForm), [formValues, serverForm]);
  const clientFieldErrors = useMemo(() => validateForm(formValues), [formValues]);
  const fieldErrors = useMemo(() => ({ ...clientFieldErrors, ...apiFieldErrors }), [clientFieldErrors, apiFieldErrors]);
  const hasWorkingChanges = Boolean(isDirty || serverDraftVersion);
  const active = serverDraftVersion || basePublishedVersion;
  const validTest = Boolean(
    !isDirty
    && serverDraftVersion
    && serverDraftVersion.test_result?.passed
    && serverDraftVersion.tested_content_hash
    && serverDraftVersion.tested_content_hash === serverDraftVersion.content_hash,
  );

  useEffect(() => {
    if (!serverSource || serverSource.id === formSourceId) return;
    setFormValues(formFromVersion(serverSource));
    setFormSourceId(serverSource.id);
    setApiFieldErrors({});
    idempotencyKey.current = crypto.randomUUID();
  }, [serverSource, formSourceId]);

  useEffect(() => {
    let cancelled = false;
    void contentHash(formValues).then((value) => {
      if (!cancelled) setCurrentContentHash(value);
    });
    return () => { cancelled = true; };
  }, [formValues]);

  useEffect(() => {
    onDirtyChange?.(hasWorkingChanges);
  }, [hasWorkingChanges, onDirtyChange]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!isDirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [isDirty]);

  if (!studio || !active || !basePublishedVersion) return null;

  function mutate(update: Partial<FormValues>) {
    setFormValues((current) => ({ ...current, ...update }));
    setApiFieldErrors({});
    setMessage('');
    setFailureDetails(null);
    idempotencyKey.current = crypto.randomUUID();
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
      await refresh();
    } catch (err: any) {
      setError(err?.message || 'Request failed');
      if (err instanceof ApiError) {
        setApiFieldErrors(err.fieldErrors);
        setFailureDetails(err);
      }
    } finally {
      setBusy(false);
    }
  }

  async function ensureDraft() {
    if (serverDraftVersion) return serverDraftVersion;
    const result = await api('/calling/allstate/script-versions/draft', { method: 'POST', body: JSON.stringify({ source_version: basePublishedVersion.version_number }) });
    return result.script as ScriptVersion;
  }

  async function saveDraft() {
    const target = await ensureDraft();
    return api(`/calling/allstate/script-versions/${target.id}`, {
      method: 'PATCH',
      body: JSON.stringify({
        ...formValues,
        change_summary: formValues.change_summary || 'Script Studio edit',
      }),
    });
  }

  async function saveAndTest() {
    const target = await ensureDraft();
    return api(`/calling/allstate/script-versions/${target.id}/save-and-test`, {
      method: 'POST',
      body: JSON.stringify({
        ...formValues,
        change_summary: formValues.change_summary || 'Script Studio edit',
      }),
    });
  }

  async function publishChanges() {
    setBusy(true);
    setPublishingStep(1);
    setError('');
    setMessage('');
    setApiFieldErrors({});
    setFailureDetails(null);
    try {
      setPublishingStep(2);
      const result = await api('/calling/allstate/script-versions/publish-changes', {
        method: 'POST',
        body: JSON.stringify({
          base_published_version_id: basePublishedVersion.id,
          form_values: formValues,
          current_content_hash: currentContentHash,
          idempotency_key: idempotencyKey.current,
        }),
      });
      const exactLiveResult = Boolean(
        result?.script?.status === 'published'
        && (
          result?.live_preview?.node_text_verified
          || result?.retell?.node_text_verification?.passed
        ),
      );
      if (!exactLiveResult) {
        throw new ApiError(502, {
          code: 'PUBLISH_VERIFICATION_MISSING',
          message: 'The publish request returned no verified live result.',
          stage: 'live_verification',
          blockers: [{
            code: 'EMPTY_OR_UNVERIFIED_RESPONSE',
            message: 'Voryx did not receive the published script and exact Retell node verification.',
          }],
          retryable: true,
          recommended_action: 'Refresh the live preview before retrying. Production was not reported as changed.',
        });
      }
      setPublishingStep(10);
      setMessage('Your changes are live in Retell and exact node text was verified.');
      await refresh();
      idempotencyKey.current = crypto.randomUUID();
      return result;
    } catch (err: any) {
      setError(err?.message || 'The script could not be published.');
      if (err instanceof ApiError) {
        setApiFieldErrors(err.fieldErrors);
        setFailureDetails(err);
      }
    } finally {
      setBusy(false);
      setPublishingStep(0);
    }
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

  const test = validTest ? serverDraftVersion?.test_result || {} : {};
  const playgroundValidation = active.publish_state?.playground_validation
    || active.test_result?.retell_playground
    || null;
  const projection = studio.cost_projection || {};
  const attentionCount = Object.values(fieldErrors).reduce((total, items) => total + items.length, 0);
  const publishDisabled = Boolean(busy || !changedFields.length || attentionCount || !currentContentHash);
  const publishReason = busy
    ? `Publishing — step ${publishingStep || 1} of 10`
    : !changedFields.length
      ? 'Make a script change before publishing.'
      : attentionCount
        ? `${attentionCount} items need attention before publishing.`
        : !currentContentHash
          ? 'Preparing the exact content hash.'
          : 'Ready to save, test, approve, publish and verify in one step.';
  const workingLabel = isDirty
    ? 'Unsaved changes — not tested or published.'
    : serverDraftVersion
      ? `Voryx v${serverDraftVersion.version_number} / ${serverDraftVersion.status} — ${serverDraftVersion.status === 'approved' ? 'ready to publish' : 'not live'}.`
      : 'No unpublished changes.';

  function requestTab(nextTab: (typeof TABS)[number]) {
    if (isDirty && nextTab !== tab) {
      setPendingTab(nextTab);
      return;
    }
    setTab(nextTab);
  }

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
          <button key={item} type="button" role="tab" aria-selected={tab === item} className={`px-3 py-2 text-sm ${tab === item ? 'border-b-2 border-emerald-500 text-emerald-200' : 'text-zinc-400'}`} onClick={() => requestTab(item)}>{item}</button>
        ))}
      </div>

      {pendingTab ? <div className="mt-4"><Notice tone="info"><div className="font-medium">You have unpublished script changes.</div><div className="mt-2 flex flex-wrap gap-2"><button type="button" className="btn-secondary" onClick={() => setPendingTab(null)}>Stay and continue editing</button><button type="button" className="btn-secondary" onClick={() => { setFormValues(formFromVersion(serverSource)); setPendingTab(null); setTab(pendingTab); }}>Discard changes</button><button type="button" className="btn" onClick={() => void action(async () => { await saveDraft(); setTab(pendingTab); setPendingTab(null); }, 'Draft saved. Retell production was not changed.')}>Save draft</button></div></Notice></div> : null}
      {message ? <div className="mt-4"><Notice tone="ok">{message}</Notice></div> : null}
      {error ? <div className="mt-4"><Notice tone="error">{error}</Notice></div> : null}
      {failureDetails ? <div className="mt-4"><Notice tone="error"><div>Stage: {failureDetails.stage || 'request'}. {failureDetails.recommendedAction || ''}</div>{failureDetails.blockers.length ? <ul className="mt-2 list-disc pl-5">{failureDetails.blockers.map((item) => <li key={`${item.code}-${item.message}`}>{item.message}</li>)}</ul> : null}</Notice></div> : null}
      {attentionCount ? <div className="mt-4"><Notice tone="error"><div className="font-medium">{attentionCount} items need attention.</div><div className="mt-2 flex flex-wrap gap-2">{Object.entries(fieldErrors).map(([field, items]) => <button type="button" className="text-left text-sm underline" key={field} onClick={() => document.getElementById(`script-${field.replaceAll('.', '-')}`)?.focus()}>{items[0]}</button>)}</div></Notice></div> : null}
      {active.status === 'failed' || active.status === 'failed_recoverable' ? (
        <div className="mt-4"><Notice tone="error">Your changes are saved but are not live. Production is still using v{studio.published_version.version_number}. Failure stage: {active.failure_stage || 'provider publish'}. {active.recovery_action || 'Retry publish or return to editing.'}</Notice></div>
      ) : null}
      {playgroundValidation?.checks?.length ? (
        <div className="mt-4 rounded border border-zinc-800 p-3" data-playground-validation>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-semibold">Retell playground checks</div>
            <div className={playgroundValidation.passed ? 'text-sm text-emerald-300' : 'text-sm text-red-300'}>{playgroundValidation.passed ? 'Passed' : 'Needs attention'}</div>
          </div>
          <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {playgroundValidation.checks.map((item: Record<string, any>) => (
              <div className={`rounded border p-2 text-sm ${item.passed ? 'border-emerald-900 text-emerald-200' : 'border-red-900 text-red-200'}`} key={item.key}>
                <div>{item.passed ? 'Pass' : 'Fail'}: {item.label}</div>
                {!item.passed && item.failure ? <div className="mt-1 text-xs">{item.failure}</div> : null}
              </div>
            ))}
          </div>
          <details className="mt-3 rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Advanced playground transcript</summary>
            <div className="mt-3 space-y-3 text-xs">
              {Object.entries(playgroundValidation.modes || {}).map(([mode, result]: [string, any]) => (
                <div key={mode}>
                  <div className="font-medium text-zinc-300">{mode.replaceAll('_', ' ')}</div>
                  {(result.turns || []).map((turn: Record<string, any>) => <div className="mt-1 rounded border border-zinc-900 p-2" key={`${mode}-${turn.turn}`}><span className="text-zinc-500">Turn {turn.turn} / node {turn.current_node_id || '-'}</span><div className="mt-1 whitespace-pre-wrap">{turn.text || '-'}</div></div>)}
                </div>
              ))}
              {playgroundValidation.wrong_person ? <div><div className="font-medium text-zinc-300">Wrong person</div><div className="mt-1 whitespace-pre-wrap">{playgroundValidation.wrong_person.text || '-'}</div></div> : null}
              {playgroundValidation.voicemail ? <div><div className="font-medium text-zinc-300">Voicemail</div><div className="mt-1 whitespace-pre-wrap">{playgroundValidation.voicemail.text || '-'}</div></div> : null}
            </div>
          </details>
        </div>
      ) : null}

      {tab === 'Script' ? (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 lg:grid-cols-2">
            <div className="rounded border border-emerald-900 bg-emerald-950/20 p-3 text-sm" data-live-retell-card>
              <div className="text-xs font-semibold text-emerald-300">LIVE IN RETELL</div>
              <div className="mt-2">Voryx v{studio.published_version.version_number} / Retell agent {studio.live_retell_preview?.agent_version ?? '-'} / flow {studio.live_retell_preview?.flow_version ?? '-'}</div>
              <div className="mt-1 text-xs text-zinc-500">Published <LocalTime value={studio.published_version.published_at} /></div>
              <div className="mt-2 text-xs text-zinc-500">Internal-test opening</div>
              <p className="mt-1 text-zinc-300">{studio.live_retell_preview?.opening_internal || studio.published_version.opening_internal}</p>
              <div className="mt-2 text-xs text-zinc-500">Consented-lead opening</div>
              <p className="mt-1 text-zinc-300">{studio.live_retell_preview?.opening_consented || studio.published_version.opening_consented}</p>
              <div className={`mt-2 text-xs ${studio.live_retell_preview?.node_text_verified ? 'text-emerald-300' : 'text-amber-300'}`}>{studio.live_retell_preview?.node_text_verified ? 'Exact Retell node text verified' : studio.live_retell_preview?.verification_error || 'Retell text verification pending'}</div>
            </div>
            <div className="rounded border border-zinc-700 p-3 text-sm" data-working-changes-card>
              <div className="text-xs font-semibold text-zinc-300">WORKING CHANGES</div>
              <div className="mt-1 text-xs text-zinc-500">DRAFT PREVIEW</div>
              <div className={`mt-2 ${isDirty ? 'text-amber-300' : ''}`}>{workingLabel}</div>
              <div className="mt-1 text-xs text-zinc-500">{changedFields.length} changed fields / {validTest ? 'Tests valid for this content.' : changedFields.length ? 'Testing required for these changes.' : 'No pending tests.'}</div>
              <p className="mt-2 text-zinc-300">{formValues.opening_consented}</p>
              {changedFields.length ? <div className="mt-2 text-xs text-amber-300">Not live until the complete verified publish finishes.</div> : null}
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Estimated prompt</div>{active.estimated_prompt_tokens} tokens</div>
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Maximum call</div>4 minutes / no retry / concurrency 1</div>
          </div>
          <label className="block space-y-1 text-sm">
            <span className="text-zinc-300">Opening style</span>
            <select id="script-opening-style" className={INPUT_CLASS} value={formValues.voice_settings?.opening_style || 'full_introduction'} onChange={(event) => mutate({ voice_settings: { ...formValues.voice_settings, opening_style: event.target.value } })}>
              <option value="full_introduction">Full introduction</option>
              <option value="confirm_person_first">Confirm person first</option>
            </select>
          </label>
          <Field id="script-opening_internal" label={formValues.voice_settings?.opening_style === 'confirm_person_first' ? 'Internal-test first-turn opening' : 'Internal-test opening'} value={formValues.opening_internal} errors={fieldErrors.opening_internal} onChange={(value) => mutate({ opening_internal: value })} />
          <Field id="script-opening_consented" label={formValues.voice_settings?.opening_style === 'confirm_person_first' ? 'Consented-lead first-turn opening' : 'Consented-lead opening'} value={formValues.opening_consented} errors={fieldErrors.opening_consented} onChange={(value) => mutate({ opening_consented: value })} />
          {formValues.voice_settings?.opening_style === 'confirm_person_first' ? (
            <div className="grid gap-3 md:grid-cols-2">
              <Field id="script-voice_settings-confirmed_person_internal" label="Confirmed-person internal introduction" value={String(formValues.voice_settings?.confirmed_person_internal || '')} errors={fieldErrors['voice_settings.confirmed_person_internal']} onChange={(value) => mutate({ voice_settings: { ...formValues.voice_settings, confirmed_person_internal: value } })} />
              <Field id="script-voice_settings-confirmed_person_consented" label="Confirmed-person prospect introduction" value={String(formValues.voice_settings?.confirmed_person_consented || '')} errors={fieldErrors['voice_settings.confirmed_person_consented']} onChange={(value) => mutate({ voice_settings: { ...formValues.voice_settings, confirmed_person_consented: value } })} />
              <Field id="script-voice_settings-wrong_person_response" label="Wrong-person response" value={String(formValues.voice_settings?.wrong_person_response || '')} errors={fieldErrors['voice_settings.wrong_person_response']} onChange={(value) => mutate({ voice_settings: { ...formValues.voice_settings, wrong_person_response: value } })} />
              <div className="rounded border border-zinc-800 p-3 text-sm text-zinc-400">Voicemail uses the separate voicemail field below and never asks the correct-person confirmation question.</div>
            </div>
          ) : null}
          <Field id="script-purpose_statement" label="Reason for call" value={formValues.purpose_statement} errors={fieldErrors.purpose_statement} onChange={(value) => mutate({ purpose_statement: value })} />
          <div className="grid gap-3 md:grid-cols-2">
            <Field id="script-discovery_content-product_interest" label="Product-interest question" value={formValues.discovery_content?.product_interest || ''} errors={fieldErrors['discovery_content.product_interest']} onChange={(value) => mutate({ discovery_content: { ...formValues.discovery_content, product_interest: value } })} />
            <Field id="script-discovery_content-coverage_review" label="Coverage-review question" value={formValues.discovery_content?.coverage_review || ''} errors={fieldErrors['discovery_content.coverage_review']} onChange={(value) => mutate({ discovery_content: { ...formValues.discovery_content, coverage_review: value } })} />
            <Field id="script-closing_library-appointment" label="Appointment close" value={formValues.closing_library?.appointment || ''} errors={fieldErrors['closing_library.appointment']} onChange={(value) => mutate({ closing_library: { ...formValues.closing_library, appointment: value } })} />
            <Field id="script-closing_library-renewal_callback" label="Renewal callback close" value={formValues.closing_library?.renewal_callback || ''} errors={fieldErrors['closing_library.renewal_callback']} onChange={(value) => mutate({ closing_library: { ...formValues.closing_library, renewal_callback: value } })} />
            <Field id="script-closing_library-busy_callback" label="Busy callback close" value={formValues.closing_library?.busy_callback || ''} errors={fieldErrors['closing_library.busy_callback']} onChange={(value) => mutate({ closing_library: { ...formValues.closing_library, busy_callback: value } })} />
            <Field id="script-voicemail_content" label="Voicemail" value={formValues.voicemail_content || ''} errors={fieldErrors.voicemail_content} onChange={(value) => mutate({ voicemail_content: value })} />
          </div>
          <Field id="script-voice_settings" label="Voice and delivery notes" value={String(formValues.voice_settings?.tone_notes || '')} errors={fieldErrors.voice_settings} onChange={(value) => mutate({ voice_settings: { ...formValues.voice_settings, tone_notes: value } })} />
          <label className="block space-y-1 text-sm"><span>Approved campaign talking points, one per line</span><textarea id="script-talking_points" className={INPUT_CLASS} rows={3} value={(formValues.talking_points || []).join('\n')} onChange={(event) => mutate({ talking_points: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean) })} /></label>
          <details className="rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Dynamic-variable preview</summary>
            <div className="mt-3 grid gap-2 text-xs md:grid-cols-2 xl:grid-cols-5">
              {['customer_name', 'agent_name', 'product_interest', 'consent_source', 'consent_date', 'renewal_month', 'slot_one', 'slot_two', 'callback_date', 'callback_time'].map((name) => <div key={name} className="rounded border border-zinc-900 p-2"><span className="font-mono text-zinc-500">{`{{${name}}}`}</span><div className="mt-1">Sample {name.replaceAll('_', ' ')}</div></div>)}
            </div>
          </details>
          <div className="space-y-2">
            <button type="button" className="btn" disabled={publishDisabled} aria-describedby="publish-changes-reason" title={publishReason} onClick={() => void publishChanges()}>{busy ? `Publishing — step ${publishingStep || 1} of 10` : active.status === 'failed' || active.status === 'failed_recoverable' ? 'Retry publish' : 'Publish changes'}</button>
            <div id="publish-changes-reason" className={publishDisabled && attentionCount ? 'text-sm text-red-300' : 'text-sm text-zinc-400'}>{publishReason}</div>
            {(active.status === 'failed' || active.status === 'failed_recoverable') ? <>
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => void action(() => api(`/calling/allstate/script-versions/${active.id}/discard`, { method: 'POST' }), 'Failed draft discarded. Live production was not changed.')}>Discard failed draft</button>
            </> : null}
          </div>
          <details className="rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Advanced actions</summary>
            <div className="mt-3 flex flex-wrap gap-2">
              <button type="button" className="btn-secondary" disabled={busy} onClick={() => void action(saveDraft, 'Draft saved. Retell production was not changed.')}>Save draft only</button>
              <button type="button" className="btn-secondary" disabled={busy || !serverDraftVersion} onClick={() => void action(() => api(`/calling/allstate/script-versions/${serverDraftVersion?.id}/test`, { method: 'POST' }), 'All 15 deterministic draft scenarios were evaluated.')}>Test only</button>
              <button type="button" className="btn-secondary" disabled={busy || !serverDraftVersion || !validTest} onClick={() => void action(() => api(`/calling/allstate/script-versions/${serverDraftVersion?.id}/request-approval`, { method: 'POST' }), 'Script approved for publishing.')}>Request approval only</button>
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
          {(formValues.objection_library || []).map((item, index) => (
            <div className="grid gap-2 border-b border-zinc-800 pb-3 md:grid-cols-[180px_110px_1fr_180px]" key={item.key}>
              <div><div className="font-medium">{item.name}</div><div className="text-xs text-zinc-500">{(item.example_phrases || []).join(' / ')}</div></div>
              <div className="text-sm"><div>{item.classification}</div><div className="text-xs text-zinc-500">Max {item.maximum_attempts}</div></div>
              <textarea className={INPUT_CLASS} rows={2} value={item.response || ''} disabled={['hard', 'DNC'].includes(item.classification)} onChange={(event) => {
                const objection_library = [...formValues.objection_library];
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
            <div className="mt-2 grid gap-2 text-sm md:grid-cols-2">{Object.entries(formValues.compliance_content || {}).filter(([key]) => key !== 'proposed_change').map(([key, value]) => <div key={key} className="flex justify-between border-b border-zinc-900 py-1"><span>{key.replaceAll('_', ' ')}</span><span>{String(value)}</span></div>)}</div>
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
