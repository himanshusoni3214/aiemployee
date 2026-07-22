'use client';

import { useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import { LocalTime } from './LocalTime';

type CallingHealth = {
  api_authenticated?: boolean;
  agent_exists?: boolean;
  agent_name?: string | null;
  outbound_agent_correctly_assigned?: boolean;
  webhook_signature_key_configured?: boolean;
  tool_token_configured?: boolean;
  internal_test_ready?: boolean;
  prospect_calling_ready?: boolean;
  agent_id?: string | null;
  agent_version?: number | string | null;
  configured_agent_version?: string | null;
  voice_id?: string | null;
  responsiveness?: number | null;
  interruption_sensitivity?: number | null;
  enable_backchannel?: boolean | null;
  backchannel_words?: string[] | null;
  ambient_sound?: string | null;
  blockers?: string[];
};

type CallingSettings = {
  from_number?: string | null;
  provider_agent_id?: string | null;
  internal_test_enabled?: boolean;
  internal_test_numbers_masked?: string[];
  prospect_calling_enabled?: boolean;
  automated_queue_enabled?: boolean;
  recording_enabled?: boolean;
  transcription_enabled?: boolean;
  call_recording_disclosure_enabled?: boolean;
  daily_call_limit?: number;
  hourly_call_limit?: number;
  concurrent_call_limit?: number;
};

type CallAttempt = {
  id: string;
  provider_call_id?: string | null;
  to_number_masked?: string | null;
  status: string;
  requested_at?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  duration_seconds?: number | null;
  termination_reason?: string | null;
  transcript?: {
    transcript?: string | null;
    segments?: unknown[];
    summary?: string | null;
    recording_url?: string | null;
    objections?: unknown[];
    extracted_fields?: Record<string, unknown>;
  } | null;
  disposition?: {
    disposition?: string | null;
    appointment_requested?: boolean;
    appointment_booked?: boolean;
    do_not_call_requested?: boolean;
    notes?: string | null;
  } | null;
  appointments?: Array<{ id: string; start_time?: string | null; timezone: string; status: string; insurance_interest?: string | null; notes?: string | null }>;
};

export type CallingWorkspace = {
  confirmation_required: string;
  settings: CallingSettings;
  health: CallingHealth;
  preview?: {
    begin_message?: string;
    consented_prospect_begin_message?: string;
    recording_disclosure?: string;
    recording_disclosure_enabled?: boolean;
    business_purpose?: string;
    dynamic_variables?: Record<string, string>;
    required_dynamic_variables?: string[];
    missing_dynamic_variables?: string[];
    override_agent_id?: string;
    override_agent_version?: string;
    from_number?: string;
    expected_agent_name?: string;
    voice?: {
      voice_id?: string;
      voice_name?: string;
      responsiveness?: number;
      interruption_sensitivity?: number;
      enable_backchannel?: boolean;
      backchannel_words?: string[];
      ambient_sound?: string | null;
      pronunciation_guidance?: Record<string, string>;
    };
  };
  warnings?: string[];
  attempts: CallAttempt[];
};

function CheckRow({ label, ok }: { label: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between rounded border border-zinc-800 px-3 py-2 text-sm">
      <span className="text-zinc-300">{label}</span>
      <span className={ok ? 'text-emerald-300' : 'text-amber-300'}>{ok ? 'Ready' : 'Blocked'}</span>
    </div>
  );
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded border border-zinc-800 p-3">
      <h2 className="text-sm font-semibold">{title}</h2>
      <div className="mt-2 text-sm text-zinc-400">{children}</div>
    </section>
  );
}

export function AllstateCallingPanel({ initialWorkspace }: { initialWorkspace: CallingWorkspace }) {
  const [workspace, setWorkspace] = useState<CallingWorkspace>(initialWorkspace);
  const [recipientName, setRecipientName] = useState('Himanshu');
  const [phoneNumber, setPhoneNumber] = useState('');
  const [insuranceInterest, setInsuranceInterest] = useState('Auto and property insurance');
  const [confirmation, setConfirmation] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selectedAttemptId, setSelectedAttemptId] = useState<string | null>(null);

  async function refresh() {
    const result = await api('/calling/allstate');
    setWorkspace(result);
  }

  useEffect(() => {
    const interval = window.setInterval(() => {
      void refresh().catch((err) => console.warn('Calling refresh failed', err));
    }, 10000);
    return () => window.clearInterval(interval);
  }, []);

  const localPhoneValid = /^\+1[2-9]\d{9}$/.test(phoneNumber.trim());
  const confirmationValid = confirmation === workspace.confirmation_required;
  const canPlaceCall = Boolean(workspace.health?.internal_test_ready && localPhoneValid && confirmationValid && !busy);
  const blockers = useMemo(() => workspace.health?.blockers || [], [workspace.health]);
  const warnings = workspace.warnings || [];
  const preview = workspace.preview || {};
  const selectedAttempt = (workspace.attempts || []).find((attempt) => attempt.id === selectedAttemptId) || null;

  async function allowNumber() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api('/calling/allstate/internal-test-number', {
        method: 'POST',
        body: JSON.stringify({ phone_number: phoneNumber, allow: true }),
      });
      setMessage('Internal test number allowlisted.');
      await refresh();
    } catch (err: any) {
      setError(err?.message || 'Could not allowlist number');
    } finally {
      setBusy(false);
    }
  }

  async function placeCall() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await api('/calling/allstate/internal-test-call', {
        method: 'POST',
        body: JSON.stringify({
          recipient_name: recipientName,
          phone_number: phoneNumber,
          insurance_interest: insuranceInterest,
          booking_timezone: 'America/Toronto',
          confirmation_text: confirmation,
        }),
      });
      setMessage(`Retell call created: ${result.retell_call_id}`);
      await refresh();
    } catch (err: any) {
      setError(err?.message || 'Call blocked');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <section className="card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm text-zinc-500">Sales Campaign &gt; Channels &gt; Calling</p>
            <h1 className="text-2xl font-semibold">Calling Channel Workspace</h1>
            <p className="text-sm text-zinc-400">Outbound-only Retell test calling. Prospect calling, batch calling, queueing and schedules are disabled.</p>
          </div>
          <div className="rounded border border-zinc-800 px-3 py-2 text-sm">
            <div>From: <span className="text-zinc-100">{workspace.settings?.from_number || 'not configured'}</span></div>
            <div>Agent: <span className="text-zinc-100">{workspace.health?.agent_name || workspace.settings?.provider_agent_id || 'not configured'}</span></div>
          </div>
        </div>
        {blockers.length ? (
          <div className="mt-4 rounded border border-amber-800 bg-amber-950/30 p-3 text-sm text-amber-200">
            <div className="font-medium">Current blockers</div>
            <ul className="mt-2 list-disc pl-5">{blockers.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        ) : null}
        {warnings.length ? (
          <div className="mt-4 rounded border border-red-800 bg-red-950/30 p-3 text-sm text-red-200">
            <div className="font-medium">Assignment warnings</div>
            <ul className="mt-2 list-disc pl-5">{warnings.map((item) => <li key={item}>{item}</li>)}</ul>
          </div>
        ) : null}
      </section>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="Provider status">
        <CheckRow label="Retell API authenticated" ok={workspace.health?.api_authenticated} />
        <CheckRow label="Voice agent configured" ok={workspace.health?.agent_exists} />
        <CheckRow label="Outbound number assigned" ok={workspace.health?.outbound_agent_correctly_assigned} />
        <CheckRow label="Webhook signature ready" ok={workspace.health?.webhook_signature_key_configured} />
        <CheckRow label="Tool token configured" ok={workspace.health?.tool_token_configured} />
        <CheckRow label="Internal test enabled" ok={workspace.settings?.internal_test_enabled} />
        <CheckRow label="Prospect calling disabled" ok={!workspace.settings?.prospect_calling_enabled} />
        <CheckRow label="Batch queue disabled" ok={!workspace.settings?.automated_queue_enabled} />
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold">Retell Call Preview</h2>
        <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <SectionCard title="Expected agent"><span className="font-mono text-xs text-zinc-200">{preview.expected_agent_name || workspace.health?.agent_name || '-'}</span></SectionCard>
          <SectionCard title="Agent ID / version"><span className="font-mono text-xs text-zinc-200">{workspace.health?.agent_id || preview.override_agent_id || '-'} / {workspace.health?.configured_agent_version || preview.override_agent_version || workspace.health?.agent_version || '-'}</span></SectionCard>
          <SectionCard title="Provider health">{workspace.health?.internal_test_ready ? 'Internal test ready' : 'Blocked'}</SectionCard>
          <SectionCard title="Recording disclosure">{preview.recording_disclosure_enabled ? 'Enabled' : 'Disabled'}</SectionCard>
          <SectionCard title="Voice tuning">{preview.voice?.voice_name || workspace.health?.voice_id || '-'} / responsiveness {preview.voice?.responsiveness ?? workspace.health?.responsiveness ?? '-'}</SectionCard>
          <SectionCard title="Ambient sound">{preview.voice?.ambient_sound || 'NONE'}</SectionCard>
        </div>
        <div className="mt-3 rounded border border-zinc-800 p-3 text-sm">
          <div className="text-zinc-500">Internal-test opening</div>
          <p className="mt-1 text-zinc-200">{preview.begin_message || '-'}</p>
        </div>
        <div className="mt-3 rounded border border-zinc-800 p-3 text-sm">
          <div className="text-zinc-500">Consented-prospect opening</div>
          <p className="mt-1 text-zinc-200">{preview.consented_prospect_begin_message || '-'}</p>
        </div>
        <div className="mt-3 rounded border border-zinc-800 p-3 text-sm">
          <div className="text-zinc-500">Recording/transcription disclosure</div>
          <p className="mt-1 text-zinc-200">{preview.recording_disclosure_enabled ? preview.recording_disclosure : 'Not announced because recording/transcription disclosure is disabled.'}</p>
        </div>
        <div className="mt-3 rounded border border-zinc-800 p-3 text-sm">
          <div className="text-zinc-500">Business purpose</div>
          <p className="mt-1 text-zinc-200">{preview.business_purpose || '-'}</p>
        </div>
        <details className="mt-3 rounded border border-zinc-800 p-3">
          <summary className="cursor-pointer text-sm font-semibold">Voice, pronunciation and dynamic variables</summary>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            <div className="text-xs text-zinc-300">Interruption sensitivity: {preview.voice?.interruption_sensitivity ?? workspace.health?.interruption_sensitivity ?? '-'}</div>
            <div className="text-xs text-zinc-300">Backchanneling: {preview.voice?.enable_backchannel ? 'Enabled' : 'Disabled'}</div>
            {Object.entries(preview.voice?.pronunciation_guidance || {}).map(([word, guide]) => (
              <div className="rounded border border-zinc-900 p-2 text-xs" key={word}>
                <div className="font-medium text-zinc-300">{word}</div>
                <div className="text-zinc-500">{guide}</div>
              </div>
            ))}
            {Object.entries(preview.dynamic_variables || {}).map(([key, value]) => (
              <div className="rounded border border-zinc-900 p-2 text-xs" key={key}>
                <div className="font-mono text-zinc-500">{key}</div>
                <div className="text-zinc-200">{value}</div>
              </div>
            ))}
          </div>
          {preview.missing_dynamic_variables?.length ? <div className="mt-3 text-sm text-red-300">Missing: {preview.missing_dynamic_variables.join(', ')}</div> : null}
        </details>
      </section>

      <section className="card">
        <h2 className="text-lg font-semibold">Internal Test</h2>
        <p className="mt-1 text-sm text-zinc-400">Use only your own approved test number. The exact confirmation is required before Voryx asks Retell to place a real phone call.</p>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="space-y-1 text-sm">Test recipient name<input className="input" value={recipientName} onChange={(event) => setRecipientName(event.target.value)} /></label>
          <label className="space-y-1 text-sm">Test phone number<input className="input" placeholder="+14165551234" value={phoneNumber} onChange={(event) => setPhoneNumber(event.target.value)} /></label>
          <label className="space-y-1 text-sm">Insurance interest<input className="input" value={insuranceInterest} onChange={(event) => setInsuranceInterest(event.target.value)} /></label>
          <label className="space-y-1 text-sm">Confirmation<input className="input" placeholder={workspace.confirmation_required} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <button type="button" className="btn-secondary" disabled={!localPhoneValid || busy} onClick={() => void allowNumber()}>Allowlist internal test number</button>
          <button type="button" className="btn" disabled={!canPlaceCall} onClick={() => void placeCall()}>Place Refined Internal Test Call</button>
        </div>
        <div className="mt-3 text-xs text-zinc-500">Allowlisted numbers: {(workspace.settings?.internal_test_numbers_masked || []).join(', ') || 'none'}</div>
        {!localPhoneValid && phoneNumber ? <div className="mt-2 text-sm text-amber-300">Use US/Canada E.164 format, for example +14165551234.</div> : null}
        {phoneNumber && localPhoneValid && !confirmationValid ? <div className="mt-2 text-sm text-amber-300">Type {workspace.confirmation_required} exactly to enable the call button.</div> : null}
        {message ? <div className="mt-3 rounded border border-emerald-800 bg-emerald-950/30 p-3 text-sm text-emerald-200">{message}</div> : null}
        {error ? <div className="mt-3 rounded border border-red-800 bg-red-950/30 p-3 text-sm text-red-200">{error}</div> : null}
      </section>

      <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <SectionCard title="Eligible leads">Phone-ready leads require valid phone, consent tied to that number, automated-call consent, no DNC/suppression and campaign approval. Current prospect-ready count: 0.</SectionCard>
        <SectionCard title="Call queue">Automated queue and schedules remain disabled during internal testing. Current queued calls: 0.</SectionCard>
        <SectionCard title="Scripts">Ava uses the refined Allstate appointment script with truthful automation disclosure and consent-safe language.</SectionCard>
        <SectionCard title="Consent and compliance">Prospect calling is disabled until consent, DNC, calling window and provider checks pass.</SectionCard>
      </section>

      <section className="card">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Call History</h2>
          <button type="button" className="btn-secondary text-xs" onClick={() => void refresh()}>Refresh</button>
        </div>
        <div className="mt-3 table-wrap">
          <table className="ops-table">
            <thead><tr><th>Timestamp</th><th>Lead/test recipient</th><th>Status</th><th>Duration</th><th>Disposition</th><th>Appointment</th><th>Provider</th><th>Action</th></tr></thead>
            <tbody>
              {(workspace.attempts || []).map((attempt) => (
                <tr key={attempt.id}>
                  <td><LocalTime value={attempt.requested_at} /></td>
                  <td>{attempt.to_number_masked || '-'}</td>
                  <td>{attempt.status}</td>
                  <td>{attempt.duration_seconds ? `${attempt.duration_seconds}s` : '-'}</td>
                  <td>{attempt.disposition?.disposition || attempt.termination_reason || '-'}</td>
                  <td>{attempt.appointments?.length ? 'Requested' : '-'}</td>
                  <td>{attempt.provider_call_id ? 'Retell' : '-'}</td>
                  <td><button type="button" className="btn-secondary text-xs" onClick={() => setSelectedAttemptId(attempt.id)}>Details</button></td>
                </tr>
              ))}
              {!workspace.attempts?.length ? <tr><td colSpan={8} className="text-zinc-400">No call attempts yet</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>

      {selectedAttempt ? (
        <section className="card" role="dialog" aria-label="Call details">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-lg font-semibold">Call details</h3>
              <p className="text-sm text-zinc-400">Status: {selectedAttempt.status} / To: {selectedAttempt.to_number_masked}</p>
            </div>
            <div className="flex gap-2">
              {selectedAttempt.transcript?.recording_url ? <a className="btn-secondary text-xs" href={selectedAttempt.transcript.recording_url}>Recording</a> : null}
              <button type="button" className="btn-secondary text-xs" onClick={() => setSelectedAttemptId(null)}>Close</button>
            </div>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Started</div><LocalTime value={selectedAttempt.started_at} /></div>
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Ended</div><LocalTime value={selectedAttempt.ended_at} /></div>
            <div className="rounded border border-zinc-800 p-3 text-sm"><div className="text-zinc-500">Disposition</div>{selectedAttempt.disposition?.disposition || '-'}</div>
          </div>
          {selectedAttempt.transcript?.summary ? <p className="mt-3 text-sm text-zinc-300">{selectedAttempt.transcript.summary}</p> : null}
          <details className="mt-3 rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Transcript and speaker segments</summary>
            {selectedAttempt.transcript?.transcript ? <pre className="mt-3 max-h-72 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-300">{selectedAttempt.transcript.transcript}</pre> : <p className="mt-2 text-sm text-zinc-400">No transcript stored.</p>}
          </details>
          <details className="mt-3 rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Objections, extracted fields and DNC</summary>
            <pre className="mt-3 max-h-72 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-300">{JSON.stringify({
              objections: selectedAttempt.transcript?.objections || [],
              extracted_fields: selectedAttempt.transcript?.extracted_fields || {},
              do_not_call_requested: selectedAttempt.disposition?.do_not_call_requested || false,
              appointment: selectedAttempt.appointments || [],
            }, null, 2)}</pre>
          </details>
          <details className="mt-3 rounded border border-zinc-800 p-3">
            <summary className="cursor-pointer text-sm font-semibold">Advanced technical details</summary>
            <pre className="mt-3 max-h-72 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-3 text-xs text-zinc-300">{JSON.stringify({
              retell_call_id: selectedAttempt.provider_call_id,
              provider: 'retell',
              termination_reason: selectedAttempt.termination_reason,
              cost: null,
            }, null, 2)}</pre>
          </details>
        </section>
      ) : null}
    </div>
  );
}
