'use client';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type ReviewItem = { lead_key: string; business?: string; email?: string; domain?: string; state: string; computed_state: string; reason?: string; can_send: boolean };
type Draft = { id: string; lead_key: string; lead_email?: string; business?: string; subject: string; body: string; status: string };
type BatchPreview = {
  coverage?: Record<string, number>;
  recipients?: Array<{ lead_key: string; business?: string; email?: string; subject?: string; sender_email?: string; reply_to_email?: string; unsubscribe_text?: string }>;
  blocked_recipients?: Array<{ lead_key: string; business?: string; email?: string; reasons?: string[] }>;
  blockers?: string[];
  limits?: Record<string, number>;
  window?: any;
  settings?: any;
  can_send_controlled_batch?: boolean;
  can_send_one_real_email?: boolean;
  prospect_emails_sent?: number;
  confirmation_required?: { send_one?: string; batch?: string };
};

function asNumber(value: unknown) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function countDrafts(drafts: Draft[]) {
  return {
    generated: drafts.length,
    approved: drafts.filter((draft) => draft.status === 'draft_approved').length,
    pending: drafts.filter((draft) => draft.status !== 'draft_approved' && draft.status !== 'draft_rejected').length,
  };
}

function statusText(ok: boolean) {
  return ok ? 'Pass' : 'Blocked';
}

function formatWindow(windowInfo: any) {
  const hours = windowInfo?.window?.hours || {};
  const days = windowInfo?.window?.days || [];
  return `${days.length ? days.join(', ') : 'Every day'} / ${hours.start || '00:00'}-${hours.end || '23:59'} ${windowInfo?.timezone || 'America/Toronto'}`;
}

export function OutreachControlsPanel({ companyId, campaignId }: { companyId: string; campaignId: string }) {
  const [settings, setSettings] = useState<any>(null);
  const [review, setReview] = useState<{ items: ReviewItem[]; counts: Record<string, number>; eligible_count: number; source_path?: string } | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [sendStatus, setSendStatus] = useState<any>(null);
  const [batchPreview, setBatchPreview] = useState<BatchPreview | null>(null);
  const [followups, setFollowups] = useState<any>(null);
  const [replyMonitor, setReplyMonitor] = useState<any>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [selectedDraftIds, setSelectedDraftIds] = useState<string[]>([]);

  async function load() {
    try {
      const [settingsData, reviewData, draftData, sendData, previewData, followData, replyData] = await Promise.all([
        api(`/companies/${companyId}/outreach-settings`),
        api(`/campaigns/${campaignId}/lead-review`),
        api(`/campaigns/${campaignId}/outreach-drafts`),
        api(`/campaigns/${campaignId}/outreach-send/status`),
        api(`/campaigns/${campaignId}/outreach/preview-batch`),
        api(`/campaigns/${campaignId}/followups/status`),
        api(`/campaigns/${campaignId}/reply-monitor/status`),
      ]);
      setSettings(settingsData);
      setReview(reviewData);
      setDrafts(draftData.drafts || []);
      setSelectedDraftIds([]);
      setSendStatus(sendData);
      setBatchPreview(previewData);
      setFollowups(followData);
      setReplyMonitor(replyData);
      setError('');
    } catch (err: any) {
      setError(err?.message || 'Outreach controls failed to load');
    }
  }

  useEffect(() => { load(); }, [companyId, campaignId]);

  async function updateSettings(next: any) {
    setBusy('settings');
    try {
      await api(`/companies/${companyId}/outreach-settings`, { method: 'PUT', body: JSON.stringify(next) });
      setMessage('Outreach settings saved');
      await load();
    } catch (err: any) { setError(err.message || 'Settings failed'); }
    finally { setBusy(''); }
  }

  async function reviewAction(item: ReviewItem, action: string) {
    setBusy(`${item.lead_key}:${action}`);
    try {
      const result = await api(`/campaigns/${campaignId}/lead-review/${item.lead_key}/${action}`, { method: 'POST', body: JSON.stringify({ reason: action }) });
      setMessage(result.message || `Lead ${action} saved`);
      await load();
    } catch (err: any) { setError(err.message || 'Lead review action failed'); }
    finally { setBusy(''); }
  }

  async function generateDrafts() {
    setBusy('generate-drafts');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach-drafts/generate`, { method: 'POST' });
      setMessage(`Draft generation complete: ${result.created || 0} created, ${result.prospect_emails_sent || 0} prospect emails sent`);
      await load();
    } catch (err: any) { setError(err.message || 'Draft generation failed'); }
    finally { setBusy(''); }
  }

  async function findLeads() {
    setBusy('find-leads');
    try {
      const result = await api(`/campaigns/${campaignId}/sales/find-leads`, { method: 'POST' });
      setMessage(result.message || `Lead research ${result.status || 'completed'}`);
      await load();
    } catch (err: any) { setError(err.message || 'Find leads failed'); }
    finally { setBusy(''); }
  }

  async function approveVisibleLeads() {
    const candidates = (review?.items || []).filter((item) => item.computed_state === 'new' && item.state !== 'approved_for_outreach').slice(0, 50);
    if (!candidates.length) {
      setMessage('No visible leads need approval.');
      return;
    }
    setBusy('approve-visible-leads');
    try {
      for (const item of candidates) {
        await api(`/campaigns/${campaignId}/lead-review/${item.lead_key}/approve`, { method: 'POST', body: JSON.stringify({ reason: 'bulk approve visible leads' }) });
      }
      setMessage(`Approved ${candidates.length} visible leads`);
      await load();
    } catch (err: any) { setError(err.message || 'Bulk lead approval failed'); }
    finally { setBusy(''); }
  }

  async function setProspectSending(enabled: boolean) {
    setBusy(enabled ? 'enable-prospect' : 'disable-prospect');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach-send/prospect-sending`, { method: 'POST', body: JSON.stringify({ enabled }) });
      setMessage(result.message || (enabled ? 'Prospect sending enabled after readiness checks' : 'Prospect sending disabled'));
      await load();
    } catch (err: any) { setError(err.message || 'Prospect sending control failed'); }
    finally { setBusy(''); }
  }

  async function previewBatch() {
    setBusy('preview-batch');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach/preview-batch`);
      setBatchPreview(result);
      setMessage(`Batch preview ready: ${result.coverage?.selected_for_batch || 0} recipients selected, ${result.prospect_emails_sent || 0} prospect emails sent`);
    } catch (err: any) { setError(err.message || 'Batch preview failed'); }
    finally { setBusy(''); }
  }

  async function sendControlledBatch() {
    setBusy('send-controlled-batch');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach/send-controlled-batch`, { method: 'POST', body: JSON.stringify({ dry_run: true }) });
      setMessage(result.message || 'Dry-run prepared. No prospect email was sent. Next: Send 1 real email or Send controlled batch.');
      setBatchPreview(result.result || null);
      await load();
    } catch (err: any) { setError(err.message || 'Controlled batch blocked'); }
    finally { setBusy(''); }
  }

  async function scheduleNextWindow() {
    setBusy('schedule-next-window');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach/schedule-next-window`, { method: 'POST', body: JSON.stringify({ limit: 5 }) });
      setMessage(`Batch scheduled for ${result.scheduled_for || 'next allowed sending window'}. Prospect emails sent: ${result.prospect_emails_sent || 0}`);
      await load();
    } catch (err: any) { setError(err.message || 'Schedule batch blocked'); }
    finally { setBusy(''); }
  }

  async function sendReal(sendOne: boolean) {
    const expected = sendOne ? 'SEND 1 REAL EMAIL' : 'SEND CONTROLLED BATCH';
    const confirmation = window.prompt(`Type ${expected} to confirm real prospect sending.`);
    if (confirmation !== expected) {
      setError(`Real send blocked. Confirmation text must be exactly: ${expected}`);
      return;
    }
    setBusy(sendOne ? 'send-one-real' : 'send-real-batch');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach/send-controlled-batch`, {
        method: 'POST',
        body: JSON.stringify({ mode: 'real_prospect_send', send_one: sendOne, confirmation, limit: sendOne ? 1 : undefined }),
      });
      setMessage(result.message || `Real prospect send completed: ${result.prospect_emails_sent || 0} sent`);
      setBatchPreview(result.result?.snapshot || null);
      await load();
    } catch (err: any) { setError(err.message || 'Real prospect send blocked'); }
    finally { setBusy(''); }
  }

  async function bulkDraftAction(action: string, draftIds: string[] = []) {
    setBusy(action);
    try {
      const result = await api(`/campaigns/${campaignId}/outreach-drafts/bulk-action`, { method: 'POST', body: JSON.stringify({ action, draft_ids: draftIds }) });
      setMessage(`Draft bulk action complete: ${result.updated || 0} updated, ${result.created || 0} created, ${result.prospect_emails_sent || 0} prospect emails sent`);
      await load();
    } catch (err: any) { setError(err.message || 'Draft bulk action failed'); }
    finally { setBusy(''); }
  }

  async function draftAction(draft: Draft, action: string) {
    setBusy(`${draft.id}:${action}`);
    try {
      const path = action === 'internal-test' ? `/outreach-drafts/${draft.id}/internal-test` : `/outreach-drafts/${draft.id}/${action}`;
      const result = await api(path, { method: 'POST' });
      setMessage(result.message || `Draft ${action} completed`);
      await load();
    } catch (err: any) { setError(err.message || `Draft ${action} failed`); }
    finally { setBusy(''); }
  }

  const settingsForm = settings || {};
  const reviewCounts = review?.counts || {};
  const draftCounts = countDrafts(drafts);
  const coverage = batchPreview?.coverage || sendStatus?.batch_preview?.coverage || {};
  const approvedLeads = coverage.approved_leads ?? reviewCounts.approved_for_outreach ?? 0;
  const approvedDrafts = coverage.approved_drafts ?? draftCounts.approved;
  const readyToSend = coverage.ready_to_send ?? 0;
  const missingDrafts = coverage.approved_leads_without_drafts ?? Math.max(0, Number(approvedLeads) - Number(draftCounts.generated));
  const recipients = batchPreview?.recipients || sendStatus?.batch_preview?.recipients || [];
  const blockedRecipients = batchPreview?.blocked_recipients || sendStatus?.batch_preview?.blocked_recipients || [];
  const canSend = Boolean(batchPreview?.can_send_controlled_batch || sendStatus?.batch_preview?.can_send_controlled_batch);
  const canSendOne = Boolean(batchPreview?.can_send_one_real_email || sendStatus?.batch_preview?.can_send_one_real_email);
  const windowInfo = batchPreview?.window || sendStatus?.batch_preview?.window || {};
  const limits = batchPreview?.limits || sendStatus?.batch_preview?.limits || {};
  const senderVerified = Boolean(settingsForm.sender_verification?.verified);
  const complianceReady = Boolean(settingsForm.physical_mailing_address && settingsForm.unsubscribe_text && settingsForm.compliance_acknowledged);
  const currentState = !senderVerified || !complianceReady ? `Sending blocked: ${sendStatus?.human_blockers?.[0] || batchPreview?.blockers?.[0] || 'sender/compliance checks incomplete'}` : !settingsForm.prospect_sending_enabled ? 'Internal test ready' : readyToSend > 0 ? 'Ready to send 1 real email' : 'Dry-run ready';
  const repliesReceived = sendStatus?.readiness?.replies_received || 0;
  const meetingsBooked = sendStatus?.readiness?.meetings_booked || 0;
  const followupsDue = followups?.followups_due || 0;
  const nextRecommendedAction = (() => {
    if (approvedLeads <= 0) return 'Approve leads before outreach';
    if (missingDrafts > 0) return `Generate drafts for ${missingDrafts} approved leads`;
    if (draftCounts.pending > 0) return `Preview and approve ${draftCounts.pending} generated drafts`;
    if (readyToSend > 0 && !settingsForm.prospect_sending_enabled) return 'Run an internal test, then enable controlled prospect sending';
    if (readyToSend > 0 && !windowInfo.allowed) return 'Schedule batch for next sending window';
    if (readyToSend > 0 && canSendOne) return 'Send 1 real email';
    if (!replyMonitor?.enabled) return 'Connect reply monitor before follow-up';
    return 'View daily report';
  })();

  const workflow = [
    { key: 'sender', label: 'Sender settings', ok: Boolean(settingsForm.sender_name && settingsForm.sender_email && settingsForm.reply_to_email), detail: settingsForm.sender_email || 'Sender email missing', action: <button className="btn-secondary text-xs" type="button" onClick={() => document.querySelector('[placeholder="Sender name"]')?.scrollIntoView({ behavior: 'smooth' })}>Edit sender settings</button> },
    { key: 'verify', label: 'Sender verification', ok: senderVerified, detail: `${settingsForm.sender_verification?.method || 'none'} / ${settingsForm.sender_verification?.sender_email || 'no account'} / ${settingsForm.sender_verification?.last_verified_at || '-'}`, action: <button className="btn-secondary text-xs" type="button" onClick={load}>Verify sender</button> },
    { key: 'compliance', label: 'Compliance settings', ok: complianceReady, detail: complianceReady ? 'Compliance acknowledged' : 'Physical address, unsubscribe text and acknowledgement required', action: <button className="btn-secondary text-xs" type="button" disabled={busy === 'settings'} onClick={() => updateSettings(settingsForm)}>Save compliance settings</button> },
    { key: 'leads', label: 'Lead approval', ok: Number(approvedLeads) > 0, detail: `total=${coverage.total_leads ?? review?.items?.length ?? 0} approved=${approvedLeads} rejected=${reviewCounts.rejected || 0} DNC=${reviewCounts.do_not_contact || 0} missing=${reviewCounts.missing_email || 0} duplicates=${reviewCounts.duplicate || 0}`, action: <div className="flex flex-wrap gap-2"><button className="btn-secondary text-xs" type="button" disabled={busy === 'find-leads'} onClick={findLeads}>Find leads</button><button className="btn-secondary text-xs" type="button" onClick={() => document.querySelector('[data-voryx-lead-review]')?.scrollIntoView({ behavior: 'smooth' })}>Review leads</button><button className="btn-secondary text-xs" type="button" disabled={busy === 'approve-visible-leads'} onClick={approveVisibleLeads}>Approve all visible leads</button></div> },
    { key: 'draft-generation', label: 'Draft generation', ok: draftCounts.generated > 0, detail: `approved leads without drafts=${missingDrafts} drafts generated=${draftCounts.generated}`, action: <button className="btn-secondary text-xs" type="button" disabled={busy === 'generate-drafts'} onClick={generateDrafts}>Generate drafts for {missingDrafts || 'approved'} leads</button> },
    { key: 'draft-approval', label: 'Draft approval', ok: Number(approvedDrafts) > 0, detail: `pending=${draftCounts.pending} approved=${approvedDrafts} ready to send=${readyToSend}`, action: <div className="flex flex-wrap gap-2"><button className="btn-secondary text-xs" type="button" onClick={() => document.querySelector('[data-voryx-draft-review]')?.scrollIntoView({ behavior: 'smooth' })}>Review drafts</button><button className="btn-secondary text-xs" type="button" disabled={busy === 'approve_all_generated'} onClick={() => bulkDraftAction('approve_all_generated')}>Approve all generated drafts</button></div> },
    { key: 'internal-test', label: 'Internal test', ok: Number(sendStatus?.readiness?.internal_tests || 0) > 0, detail: `${sendStatus?.readiness?.internal_tests || 0} prepared/sent. Recipient hard-limited to himanshusoni3214@gmail.com`, action: <button className="btn-secondary text-xs" type="button" disabled={!drafts.some((draft) => draft.status === 'draft_approved')} onClick={() => { const draft = drafts.find((item) => item.status === 'draft_approved'); if (draft) void draftAction(draft, 'internal-test'); }}>Prep internal test</button> },
    { key: 'enable', label: 'Enable prospect sending', ok: Boolean(settingsForm.prospect_sending_enabled), detail: settingsForm.prospect_sending_enabled ? 'Synced to DB and Hermes workspace/jobs safety policy' : 'Off until readiness passes', action: <div className="flex flex-wrap gap-2"><button className="btn-secondary text-xs" type="button" disabled={busy === 'enable-prospect' || !sendStatus?.readiness?.can_enable_prospect_sending || settingsForm.prospect_sending_enabled} onClick={() => setProspectSending(true)}>Enable</button><button className="btn-secondary text-xs" type="button" disabled={busy === 'disable-prospect' || !settingsForm.prospect_sending_enabled} onClick={() => setProspectSending(false)}>Disable</button></div> },
    { key: 'batch', label: 'Send controlled batch', ok: canSend, detail: `approved leads=${approvedLeads} approved drafts=${approvedDrafts} ready=${readyToSend} daily remaining=${limits.daily_remaining ?? 0}`, action: <div className="flex flex-wrap gap-2"><button className="btn-secondary text-xs" type="button" disabled={busy === 'preview-batch'} onClick={previewBatch}>Preview email batch</button><button className="btn-secondary text-xs" type="button" disabled={!readyToSend || busy === 'send-controlled-batch'} onClick={sendControlledBatch}>Dry-run prepare</button>{canSendOne ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'send-one-real'} onClick={() => sendReal(true)}>Send 1 real email</button> : null}{canSend ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'send-real-batch'} onClick={() => sendReal(false)}>Send controlled batch</button> : null}<button className="btn-secondary text-xs" type="button" disabled={!readyToSend || busy === 'schedule-next-window'} onClick={scheduleNextWindow}>Schedule batch for next sending window</button></div> },
    { key: 'followup', label: 'Reply monitor / follow-up', ok: false, detail: followups?.reason || 'Disabled until Gmail/thread monitoring is connected', action: <button className="btn-secondary text-xs" type="button" disabled>Connect Gmail / Verify thread tracking</button> },
  ];

  return (
    <div className="mt-3 grid gap-3 rounded border border-zinc-800 p-3" data-voryx-outreach-controls data-voryx-ai-sales-control-center>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">AI Sales Employee Control Center</h3>
          <p className="text-xs text-zinc-500">Current state: {currentState}. One approval-based workflow for leads, drafts, controlled sending, reports, and follow-up readiness.</p>
        </div>
        <div className={canSend ? 'text-xs text-emerald-400' : 'text-xs text-amber-300'}>{canSend ? 'Ready for controlled batch' : (sendStatus?.human_blockers?.[0] || batchPreview?.blockers?.[0] || 'Sending blocked')}</div>
      </div>
      <div className="rounded border border-emerald-900 bg-emerald-950/20 p-3" data-voryx-next-recommended-action>
        <div className="text-xs uppercase text-emerald-300">Next recommended action</div>
        <div className="mt-1 text-lg font-semibold text-zinc-100">{nextRecommendedAction}</div>
        {!windowInfo.allowed ? <div className="mt-2 text-xs text-amber-200">Current time: {windowInfo.local_now || '-'} / Allowed window: {formatWindow(windowInfo)} / Next allowed send time: {windowInfo.next_allowed_send_at || '-'}</div> : null}
      </div>
      <div className="grid gap-2 md:grid-cols-3 xl:grid-cols-5" data-voryx-sales-employee-stats>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Leads found</p><p className="text-xl font-semibold">{coverage.total_leads ?? review?.items?.length ?? 0}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Leads approved</p><p className="text-xl font-semibold">{approvedLeads}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Drafts generated</p><p className="text-xl font-semibold">{draftCounts.generated}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Drafts approved</p><p className="text-xl font-semibold">{approvedDrafts}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Ready to send</p><p className="text-xl font-semibold">{readyToSend}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Emails sent today</p><p className="text-xl font-semibold">{limits.daily_sent ?? 0}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Replies received</p><p className="text-xl font-semibold">{asNumber(repliesReceived)}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Meetings booked</p><p className="text-xl font-semibold">{asNumber(meetingsBooked)}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Follow-ups due</p><p className="text-xl font-semibold">{asNumber(followupsDue)}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Daily limit</p><p className="text-xl font-semibold">{limits.daily_limit ?? settingsForm.daily_send_limit ?? 5}</p></div>
      </div>
      <div className="flex flex-wrap gap-2" data-voryx-primary-sales-actions>
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'find-leads'} onClick={findLeads}>Find leads</button>
        <button className="btn-secondary text-xs" type="button" onClick={() => document.querySelector('[data-voryx-lead-review]')?.scrollIntoView({ behavior: 'smooth' })}>Review leads</button>
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'generate-drafts'} onClick={generateDrafts}>Generate drafts</button>
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'preview-batch'} onClick={previewBatch}>Preview email batch</button>
        {canSendOne ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'send-one-real'} onClick={() => sendReal(true)}>Send 1 real email</button> : null}
        {canSend ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'send-real-batch'} onClick={() => sendReal(false)}>Send controlled batch</button> : null}
        <button className="btn-secondary text-xs" type="button" disabled={!readyToSend || busy === 'schedule-next-window'} onClick={scheduleNextWindow}>Schedule batch</button>
        <button className="btn-secondary text-xs" type="button" disabled>View replies</button>
        <button className="btn-secondary text-xs" type="button" onClick={() => document.querySelector('[data-voryx-daily-report]')?.scrollIntoView({ behavior: 'smooth' })}>View report</button>
      </div>
      {error ? <div className="rounded border border-red-900 bg-red-950/40 p-2 text-xs text-red-200">{error}</div> : null}
      {message ? <div className="rounded border border-emerald-900 bg-emerald-950/30 p-2 text-xs text-emerald-200">{message}</div> : null}

      <div className="grid gap-2 md:grid-cols-3">
        <input className="input" placeholder="Sender name" value={settingsForm.sender_name || ''} onChange={(e) => setSettings({ ...settingsForm, sender_name: e.target.value })} />
        <input className="input" placeholder="Sender email" value={settingsForm.sender_email || ''} onChange={(e) => setSettings({ ...settingsForm, sender_email: e.target.value })} />
        <input className="input" placeholder="Reply-to email" value={settingsForm.reply_to_email || ''} onChange={(e) => setSettings({ ...settingsForm, reply_to_email: e.target.value })} />
        <input className="input" placeholder="Physical mailing address" value={settingsForm.physical_mailing_address || ''} onChange={(e) => setSettings({ ...settingsForm, physical_mailing_address: e.target.value })} />
        <input className="input" placeholder="Unsubscribe text" value={settingsForm.unsubscribe_text || ''} onChange={(e) => setSettings({ ...settingsForm, unsubscribe_text: e.target.value })} />
        <input className="input" type="number" min="1" max="5" placeholder="Daily limit" value={settingsForm.daily_send_limit || 5} onChange={(e) => setSettings({ ...settingsForm, daily_send_limit: Number(e.target.value || 5) })} />
      </div>
      <div className="grid gap-1 rounded border border-zinc-800 p-2 text-xs text-zinc-400" data-voryx-sender-verification>
        <div>Sender verification: <span className={senderVerified ? 'text-emerald-300' : 'text-amber-300'}>{senderVerified ? 'Verified' : 'Not verified'}</span></div>
        <div>Method: <span className="text-zinc-200">{settingsForm.sender_verification?.method || 'none'}</span></div>
        <div>Account: <span className="text-zinc-200">{settingsForm.sender_verification?.sender_email || '-'}</span></div>
        <div>Last verified: <span className="text-zinc-200">{settingsForm.sender_verification?.last_verified_at || '-'}</span></div>
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-zinc-300">
        <label><input type="checkbox" checked={Boolean(settingsForm.compliance_acknowledged)} onChange={(e) => setSettings({ ...settingsForm, compliance_acknowledged: e.target.checked })} /> Compliance acknowledged</label>
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'settings'} onClick={() => updateSettings(settingsForm)}>Save settings</button>
      </div>

      <div className="grid gap-2 rounded border border-zinc-800 p-2 text-xs" data-voryx-outreach-readiness>
        <div className="font-semibold text-zinc-200">Lead-to-Email Workflow</div>
        {workflow.map((step) => <div className="grid gap-2 border-t border-zinc-900 pt-2 md:grid-cols-[10rem_1fr_auto]" key={step.key}><span className={step.ok ? 'text-emerald-300' : 'text-amber-300'}>{statusText(step.ok)}: {step.label}</span><span className="text-zinc-500">{step.detail}</span><span>{step.action}</span></div>)}
      </div>

      <div className="grid gap-2 rounded border border-zinc-800 p-2 text-xs" data-voryx-batch-preview>
        <div className="font-semibold text-zinc-200">Batch Preview</div>
        <div className="text-zinc-400">Approved leads: {approvedLeads} / Drafts generated: {draftCounts.generated} / Approved drafts: {approvedDrafts} / Ready to send: {readyToSend} / Missing drafts: {missingDrafts} / Pending draft approval: {draftCounts.pending}</div>
        <div className="text-zinc-400">Allowed window: {formatWindow(windowInfo)} / Current time: {windowInfo.local_now || '-'} / Next allowed send: {windowInfo.next_allowed_send_at || (windowInfo.allowed ? 'now' : '-')}</div>
        <div className="text-zinc-400">Daily remaining: {limits.daily_remaining ?? 0} / Hourly remaining: {limits.hourly_remaining ?? 0}</div>
        <div className="text-zinc-500">Real send confirmations: SEND 1 REAL EMAIL / SEND CONTROLLED BATCH</div>
        <div className="grid gap-1">
          {recipients.slice(0, 5).map((item) => <div className="rounded bg-zinc-950 p-2" key={item.lead_key}><div className="text-zinc-200">{item.business || item.lead_key} / {item.email}</div><div className="text-zinc-500">{item.subject}</div><div className="text-zinc-500">From {item.sender_email} / Reply-to {item.reply_to_email} / {item.unsubscribe_text}</div></div>)}
          {!recipients.length ? <div className="text-zinc-500">No recipients selected. Use Preview batch after leads and drafts are approved.</div> : null}
        </div>
        {blockedRecipients.length ? <div className="text-zinc-500">Blocked recipients: {blockedRecipients.slice(0, 5).map((item) => `${item.business || item.email || item.lead_key}: ${(item.reasons || []).join(', ')}`).join(' / ')}</div> : null}
        {batchPreview?.blockers?.length ? <div className="text-amber-300">Batch blockers: {batchPreview.blockers.join(' / ')}</div> : null}
      </div>

      <div className="grid gap-2 text-xs text-zinc-400" data-voryx-lead-review>
        <div>Lead source: <span className="text-zinc-200">{review?.source_path || 'No CSV source found'}</span></div>
        <div>Lead queue: {Object.entries(reviewCounts).map(([k, v]) => `${k}=${v}`).join(' / ') || '0'} / eligible={review?.eligible_count || 0}</div>
      </div>
      <div className="max-h-72 overflow-auto">
        <table className="ops-table text-xs">
          <thead><tr><th>Lead</th><th>Email</th><th>State</th><th>Action</th></tr></thead>
          <tbody>
            {(review?.items || []).slice(0, 20).map((item) => <tr key={item.lead_key}>
              <td>{item.business || item.lead_key}</td><td>{item.email || '-'}</td><td>{item.state}{item.reason ? ` / ${item.reason}` : ''}</td>
              <td className="space-x-1">
                <button className="btn-secondary text-xs" type="button" disabled={busy.startsWith(item.lead_key) || ['missing_email','duplicate','do_not_contact'].includes(item.computed_state)} onClick={() => reviewAction(item, 'approve')}>Approve</button>
                <button className="btn-secondary text-xs" type="button" disabled={busy.startsWith(item.lead_key)} onClick={() => reviewAction(item, 'reject')}>Reject</button>
                <button className="btn-secondary text-xs" type="button" disabled={busy.startsWith(item.lead_key)} onClick={() => reviewAction(item, 'do-not-contact')}>DNC</button>
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>

      <div className="grid gap-2" data-voryx-draft-review>
        <div className="flex flex-wrap gap-2 text-xs">
          <button className="btn-secondary text-xs" type="button" onClick={() => bulkDraftAction('approve_all_generated')}>Approve all generated drafts for approved leads</button>
          <button className="btn-secondary text-xs" type="button" disabled={!selectedDraftIds.length} onClick={() => bulkDraftAction('approve_selected', selectedDraftIds)}>Approve selected drafts</button>
          <button className="btn-secondary text-xs" type="button" disabled={!selectedDraftIds.length} onClick={() => bulkDraftAction('reject_selected', selectedDraftIds)}>Reject selected drafts</button>
          <button className="btn-secondary text-xs" type="button" disabled={!selectedDraftIds.length} onClick={() => bulkDraftAction('regenerate_selected', selectedDraftIds)}>Regenerate selected drafts</button>
          <span className="text-zinc-500">Auto-approve after preview: off by default. Final send still requires typed confirmation.</span>
        </div>
        {drafts.slice(0, 10).map((draft) => <div className="rounded border border-zinc-800 p-2" key={draft.id}>
          <div className="flex flex-wrap justify-between gap-2 text-xs"><label className="flex items-center gap-2"><input type="checkbox" checked={selectedDraftIds.includes(draft.id)} onChange={(event) => setSelectedDraftIds(event.target.checked ? [...selectedDraftIds, draft.id] : selectedDraftIds.filter((id) => id !== draft.id))} /><strong>{draft.subject}</strong></label><span>{draft.status}</span></div>
          <pre className="mt-2 whitespace-pre-wrap text-xs text-zinc-400">{draft.body}</pre>
          <div className="mt-2 flex flex-wrap gap-2">
            <button className="btn-secondary text-xs" type="button" onClick={() => draftAction(draft, 'approve')}>Approve draft</button>
            <button className="btn-secondary text-xs" type="button" onClick={() => draftAction(draft, 'reject')}>Reject draft</button>
            <button className="btn-secondary text-xs" type="button" disabled={draft.status !== 'draft_approved'} onClick={() => draftAction(draft, 'internal-test')}>Internal test</button>
          </div>
        </div>)}
      </div>
      <div className="grid gap-1 text-xs text-zinc-500">
        <div>Prospect send status: {(sendStatus?.human_blockers || []).join(' / ') || 'Ready for controlled sending'}</div>
        <div>Follow-up: {followups?.state || 'unknown'}{followups?.reason ? ` / ${followups.reason}` : ''}</div>
        <div>Reply Monitor: {replyMonitor?.state || 'unknown'}{replyMonitor?.reason ? ` / ${replyMonitor.reason}` : ''}</div>
        <div>Calling: not connected. Required before enabling: voice provider, caller ID, call script, recording/transcript policy, do-not-call controls and daily call limit.</div>
        <div>Prospect emails sent during dashboard QA actions: 0</div>
      </div>
    </div>
  );
}
