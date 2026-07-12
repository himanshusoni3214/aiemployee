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
type OutreachMode = 'lead_research' | 'email_outreach' | 'full';

const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function countDrafts(drafts: Draft[], allowedLeadKeys?: Set<string>) {
  const activeDrafts = drafts.filter((draft) => draft.status !== 'draft_rejected' && (!allowedLeadKeys || allowedLeadKeys.has(draft.lead_key)));
  return {
    generated: activeDrafts.length,
    approved: activeDrafts.filter((draft) => draft.status === 'draft_approved').length,
    editable: activeDrafts.length,
    rejected: drafts.length - activeDrafts.length,
  };
}

function formatWindow(windowInfo: any) {
  const hours = windowInfo?.window?.hours || {};
  const days = windowInfo?.window?.days || [];
  const dates = windowInfo?.window?.dates || {};
  const dateText = dates.start || dates.end ? ` / ${dates.start || 'any'} to ${dates.end || 'open'}` : '';
  return `${days.length ? days.join(', ') : 'Every day'} / ${hours.start || '00:00'}-${hours.end || '23:59'}${dateText} ${windowInfo?.timezone || 'America/Toronto'}`;
}

function firstBlocker(sendStatus: any, batchPreview: BatchPreview | null) {
  return sendStatus?.human_blockers?.[0] || batchPreview?.blockers?.[0] || '';
}

function draftMissingRequiredFooter(body: string, unsubscribeText: string) {
  const required = (unsubscribeText || '').trim();
  return Boolean(required && !(body || '').includes(required));
}

export function OutreachControlsPanel({
  companyId,
  campaignId,
  mode = 'full',
  leadSourceCampaignId,
  reportHref,
}: {
  companyId: string;
  campaignId: string;
  mode?: OutreachMode;
  leadSourceCampaignId?: string;
  reportHref?: string;
}) {
  const [settings, setSettings] = useState<any>(null);
  const [review, setReview] = useState<{ items: ReviewItem[]; counts: Record<string, number>; eligible_count: number; source_path?: string } | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [sendStatus, setSendStatus] = useState<any>(null);
  const [batchPreview, setBatchPreview] = useState<BatchPreview | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [draftEdits, setDraftEdits] = useState<Record<string, { subject: string; body: string }>>({});
  const [showAllLeads, setShowAllLeads] = useState(false);

  async function load() {
    try {
      const reviewCampaignId = leadSourceCampaignId || campaignId;
      const [settingsData, reviewData, draftData, sendData, previewData] = await Promise.all([
        api(`/companies/${companyId}/outreach-settings`),
        api(`/campaigns/${reviewCampaignId}/lead-review`),
        api(`/campaigns/${campaignId}/outreach-drafts`),
        api(`/campaigns/${campaignId}/outreach-send/status`),
        api(`/campaigns/${campaignId}/outreach/preview-batch`),
      ]);
      setSettings(settingsData);
      setReview(reviewData);
      setDrafts(draftData.drafts || []);
      setSendStatus(sendData);
      setBatchPreview(previewData);
      setDraftEdits(Object.fromEntries((draftData.drafts || []).map((draft: Draft) => [draft.id, { subject: draft.subject, body: draft.body }])));
      setError('');
    } catch (err: any) {
      setError(err?.message || 'Email marketing controls failed to load');
    }
  }

  useEffect(() => { load(); }, [companyId, campaignId, leadSourceCampaignId]);

  async function updateSettings(next: any) {
    setBusy('settings');
    try {
      await api(`/companies/${companyId}/outreach-settings`, { method: 'PUT', body: JSON.stringify(next) });
      setMessage('Email settings saved');
      await load();
    } catch (err: any) { setError(err.message || 'Settings failed'); }
    finally { setBusy(''); }
  }

  function updateAllowedDay(day: string, checked: boolean) {
    const current = Array.isArray(settingsForm.allowed_sending_days) ? settingsForm.allowed_sending_days : [];
    const allowed_sending_days = checked ? Array.from(new Set([...current, day])) : current.filter((item: string) => item !== day);
    setSettings({ ...settingsForm, allowed_sending_days });
  }

  function updateAllowedHour(key: 'start' | 'end', value: string) {
    setSettings({ ...settingsForm, allowed_sending_hours: { ...(settingsForm.allowed_sending_hours || {}), [key]: value } });
  }

  async function findLeads() {
    setBusy('find-leads');
    try {
      const result = await api(`/campaigns/${campaignId}/sales/find-leads`, { method: 'POST' });
      setMessage(result.message || 'Lead generation finished');
      await load();
    } catch (err: any) { setError(err.message || 'Lead generation failed'); }
    finally { setBusy(''); }
  }

  async function approveEligibleLeads() {
    const candidates = (review?.items || []).filter((item) => item.computed_state === 'new' && item.state !== 'approved_for_outreach');
    if (!candidates.length) {
      setMessage('No current eligible leads need approval.');
      return;
    }
    setBusy('approve-eligible-leads');
    try {
      for (const item of candidates) {
        await api(`/campaigns/${campaignId}/lead-review/${item.lead_key}/approve`, { method: 'POST', body: JSON.stringify({ reason: 'approved from email employee workflow' }) });
      }
      setMessage(`Approved ${candidates.length} eligible current leads`);
      await load();
    } catch (err: any) { setError(err.message || 'Lead approval failed'); }
    finally { setBusy(''); }
  }

  async function reviewAction(item: ReviewItem, action: string) {
    setBusy(`${item.lead_key}:${action}`);
    try {
      const reviewCampaignId = leadSourceCampaignId || campaignId;
      const result = await api(`/campaigns/${reviewCampaignId}/lead-review/${item.lead_key}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ reason: action, target_campaign_id: campaignId }),
      });
      setMessage(result.message || `Lead ${action} saved`);
      await load();
    } catch (err: any) { setError(err.message || 'Lead review action failed'); }
    finally { setBusy(''); }
  }

  async function generateDrafts() {
    setBusy('generate-drafts');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach-drafts/generate`, {
        method: 'POST',
        body: JSON.stringify({ source_campaign_id: leadSourceCampaignId || campaignId }),
      });
      setMessage(`Email drafts ready: ${result.created || 0} created. No prospect email sent.`);
      await load();
    } catch (err: any) { setError(err.message || 'Email draft generation failed'); }
    finally { setBusy(''); }
  }

  async function approveAllDrafts() {
    setBusy('approve_all_generated');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach-drafts/bulk-action`, { method: 'POST', body: JSON.stringify({ action: 'approve_all_generated', draft_ids: [] }) });
      setMessage(`Approved ${result.updated || 0} drafts. Compliance footer is enforced automatically. No prospect email sent.`);
      await load();
    } catch (err: any) { setError(err.message || 'Draft approval failed'); }
    finally { setBusy(''); }
  }

  async function saveDraft(draft: Draft) {
    setBusy(`save-draft:${draft.id}`);
    try {
      const edit = draftEdits[draft.id] || { subject: draft.subject, body: draft.body };
      await api(`/outreach-drafts/${draft.id}`, { method: 'PUT', body: JSON.stringify(edit) });
      setMessage('Draft updated');
      await load();
    } catch (err: any) { setError(err.message || 'Draft update failed'); }
    finally { setBusy(''); }
  }

  async function approveDraft(draft: Draft) {
    setBusy(`approve-draft:${draft.id}`);
    try {
      await api(`/outreach-drafts/${draft.id}/approve`, { method: 'POST' });
      setMessage('Draft approved');
      await load();
    } catch (err: any) { setError(err.message || 'Draft approval failed'); }
    finally { setBusy(''); }
  }

  async function sendTest() {
    const draft = drafts.find((item) => item.status === 'draft_approved') || drafts[0];
    if (!draft) {
      setError('Create an email draft before sending a test.');
      return;
    }
    setBusy('internal-test');
    try {
      if (draft.status !== 'draft_approved') {
        await api(`/outreach-drafts/${draft.id}/approve`, { method: 'POST' });
      }
      const result = await api(`/outreach-drafts/${draft.id}/internal-test`, { method: 'POST' });
      setMessage(result.message || `Test prepared for ${result.recipient || 'approved internal recipient'}. No prospect email sent.`);
      await load();
    } catch (err: any) { setError(err.message || 'Test email failed'); }
    finally { setBusy(''); }
  }

  async function setProspectSending(enabled: boolean) {
    setBusy(enabled ? 'enable-prospect' : 'disable-prospect');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach-send/prospect-sending`, { method: 'POST', body: JSON.stringify({ enabled }) });
      setMessage(result.message || (enabled ? 'Sending enabled' : 'Sending disabled'));
      await load();
    } catch (err: any) { setError(err.message || 'Sending control failed'); }
    finally { setBusy(''); }
  }

  async function sendApprovedEmails() {
    const expected = 'SEND CONTROLLED BATCH';
    const confirmation = window.prompt(`Type ${expected} to send approved emails.`);
    if (confirmation !== expected) {
      setError(`Send blocked. Confirmation text must be exactly: ${expected}`);
      return;
    }
    setBusy('send-real-batch');
    try {
      const result = await api(`/campaigns/${campaignId}/outreach/send-controlled-batch`, {
        method: 'POST',
        body: JSON.stringify({ mode: 'real_prospect_send', confirmation }),
      });
      setMessage(result.message || `Send complete: ${result.prospect_emails_sent || 0} emails sent`);
      await load();
    } catch (err: any) { setError(err.message || 'Send blocked'); }
    finally { setBusy(''); }
  }

  const settingsForm = settings || {};
  const showLeadWorkflow = mode === 'lead_research' || mode === 'full';
  const showEmailWorkflow = mode === 'email_outreach' || mode === 'full';
  const allowedDays = Array.isArray(settingsForm.allowed_sending_days) ? settingsForm.allowed_sending_days : [];
  const allowedHours = settingsForm.allowed_sending_hours || {};
  const reviewCounts = review?.counts || {};
  const approvedSourceLeadKeys = showEmailWorkflow && leadSourceCampaignId
    ? new Set((review?.items || []).filter((item) => item.state === 'approved_for_outreach').map((item) => item.lead_key))
    : undefined;
  const draftCounts = countDrafts(drafts, approvedSourceLeadKeys);
  const coverage = batchPreview?.coverage || sendStatus?.batch_preview?.coverage || {};
  const approvedLeads = Number(coverage.approved_leads ?? reviewCounts.approved_for_outreach ?? 0);
  const sourceApprovedLeads = Number(reviewCounts.approved_for_outreach ?? 0);
  const approvedLeadsForActions = showEmailWorkflow && leadSourceCampaignId ? sourceApprovedLeads : approvedLeads;
  const leadsFoundForDisplay = showEmailWorkflow && leadSourceCampaignId ? Number(review?.items?.length ?? 0) : Number(coverage.total_leads ?? review?.items?.length ?? 0);
  const readyToSend = Number(coverage.ready_to_send ?? 0);
  const missingDrafts = Number(coverage.approved_leads_without_drafts ?? Math.max(0, approvedLeadsForActions - draftCounts.generated));
  const canSend = Boolean(batchPreview?.can_send_controlled_batch || sendStatus?.batch_preview?.can_send_controlled_batch);
  const windowInfo = batchPreview?.window || sendStatus?.batch_preview?.window || {};
  const limits = batchPreview?.limits || sendStatus?.batch_preview?.limits || {};
  const allReviewItems = review?.items || [];
  const visibleLeads = showAllLeads ? allReviewItems : allReviewItems.slice(0, 20);
  const hiddenLeadCount = Math.max(0, allReviewItems.length - visibleLeads.length);
  const visibleDrafts = drafts.filter((draft) => draft.status !== 'draft_rejected' && (!approvedSourceLeadKeys || approvedSourceLeadKeys.has(draft.lead_key))).slice(0, 5);
  const blocker = firstBlocker(sendStatus, batchPreview);
  const nextStep = (() => {
    if (!showEmailWorkflow) {
      if (!review?.items?.length) return 'Generate leads';
      if (approvedLeads <= 0) return 'Review and approve real leads';
      return 'Lead research complete for approved leads';
    }
    if (!review?.items?.length) return 'Use approved Lead Research leads or connect this workflow to a lead source';
    if (approvedLeadsForActions <= 0) return 'Approve leads in Lead Research first';
    if (missingDrafts > 0 || !draftCounts.generated) return 'Generate email drafts';
    if (draftCounts.approved <= 0) return 'Review drafts, then approve all drafts or edit one draft';
    if (!sendStatus?.readiness?.internal_tests) return 'Send a test email';
    if (!settingsForm.prospect_sending_enabled) return 'Turn on sending when ready';
    if (!canSend) return blocker || 'Sending is blocked until readiness passes';
    return 'Send approved emails';
  })();

  return (
    <div className="mt-3 grid gap-4 rounded border border-zinc-800 p-3" data-voryx-outreach-controls data-voryx-email-marketing-employee data-voryx-outreach-mode={mode}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">{showEmailWorkflow ? 'Email Marketing Workflow' : 'Lead Research Workflow'}</h3>
          <p className="text-xs text-zinc-500">{showEmailWorkflow ? 'Simple flow: review approved leads, generate drafts, test, send, report.' : 'Generate real leads, inspect source evidence, then approve only valid contacts.'}</p>
        </div>
        <div className="rounded border border-emerald-900 bg-emerald-950/20 px-3 py-2 text-xs text-emerald-200" data-voryx-next-recommended-action>
          Next: <span className="font-semibold">{nextStep}</span>
        </div>
      </div>

      {error ? <div className="rounded border border-red-900 bg-red-950/40 p-2 text-xs text-red-200">{error}</div> : null}
      {message ? <div className="rounded border border-emerald-900 bg-emerald-950/30 p-2 text-xs text-emerald-200">{message}</div> : null}

      <div className="grid gap-2 md:grid-cols-5" data-voryx-email-stats>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Leads found</p><p className="text-xl font-semibold">{leadsFoundForDisplay}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Approved</p><p className="text-xl font-semibold">{approvedLeadsForActions}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Drafts</p><p className="text-xl font-semibold">{draftCounts.generated}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Ready to send</p><p className="text-xl font-semibold">{readyToSend}</p></div>
        <div className="rounded border border-zinc-800 p-2"><p className="text-xs text-zinc-500">Sent today</p><p className="text-xl font-semibold">{limits.daily_sent ?? 0}</p></div>
      </div>

      <div className="flex flex-wrap gap-2" data-voryx-simple-email-actions>
        {showLeadWorkflow ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'find-leads'} onClick={findLeads}>Generate leads</button> : null}
        {showLeadWorkflow ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'approve-eligible-leads'} onClick={approveEligibleLeads}>Approve all eligible leads</button> : null}
        {showEmailWorkflow ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'generate-drafts' || approvedLeadsForActions <= 0} onClick={generateDrafts}>Generate email draft</button> : null}
        {showEmailWorkflow ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'approve_all_generated' || !draftCounts.generated} onClick={approveAllDrafts}>Approve all drafts</button> : null}
        {showEmailWorkflow ? <button className="btn-secondary text-xs" type="button" disabled={busy === 'internal-test' || !drafts.length} onClick={sendTest}>Send test</button> : null}
        {showEmailWorkflow ? <button className="btn text-xs" type="button" disabled={!canSend || busy === 'send-real-batch'} title={!canSend ? (blocker || 'Complete the previous steps before sending') : 'Send approved emails through Hermes/Himalaya'} onClick={sendApprovedEmails}>Send approved emails</button> : null}
        <a className="btn-secondary text-xs" href={reportHref || `/reports?company_id=${companyId}`}>Report</a>
      </div>
      {showEmailWorkflow ? <div className="rounded border border-amber-900 bg-amber-950/20 p-2 text-xs text-amber-200">Send only to verified, real public inboxes. Current lead rows do not include a separate email-verification flag, so do not treat assumed addresses as safe.</div> : null}
      {showEmailWorkflow && !canSend && readyToSend > 0 ? <div className="rounded border border-amber-900 bg-amber-950/20 p-2 text-xs text-amber-200">Send is blocked: {blocker || 'readiness checks are incomplete'}. {windowInfo?.allowed === false ? `Allowed window: ${formatWindow(windowInfo)}. Next allowed send: ${windowInfo.next_allowed_send_at || '-'}.` : null}</div> : null}

      <section className="rounded border border-zinc-800 p-3" data-voryx-lead-review>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-sm font-semibold">Leads</h4>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-zinc-500">{review?.source_path || 'No lead file loaded'}</span>
            {hiddenLeadCount > 0 ? <button className="btn-secondary text-xs" type="button" onClick={() => setShowAllLeads(true)}>Show all {allReviewItems.length} leads</button> : null}
            {showAllLeads && allReviewItems.length > 20 ? <button className="btn-secondary text-xs" type="button" onClick={() => setShowAllLeads(false)}>Show first 20</button> : null}
          </div>
        </div>
        <div className="max-h-72 overflow-auto">
          <table className="ops-table text-xs">
            <thead><tr><th>Business</th><th>Email</th><th>Status</th><th>Action</th></tr></thead>
            <tbody>
              {visibleLeads.map((item) => <tr key={item.lead_key}>
                <td>{item.business || item.lead_key}</td>
                <td>{item.email || '-'}</td>
                <td>{item.state}{item.reason ? ` / ${item.reason}` : ''}</td>
                <td className="space-x-1">
                  <button className="btn-secondary text-xs" type="button" disabled={busy.startsWith(item.lead_key) || item.state === 'approved_for_outreach' || ['missing_email','duplicate','do_not_contact'].includes(item.computed_state)} onClick={() => reviewAction(item, 'approve')}>Approve</button>
                  <button className="btn-secondary text-xs" type="button" disabled={busy.startsWith(item.lead_key)} onClick={() => reviewAction(item, 'reject')}>Reject</button>
                </td>
              </tr>)}
              {!visibleLeads.length ? <tr><td colSpan={4} className="text-zinc-500">{showEmailWorkflow ? 'No approved lead source is connected yet.' : 'No leads yet. Generate leads first.'}</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>

      {showEmailWorkflow ? <section className="grid gap-2 rounded border border-zinc-800 p-3" data-voryx-draft-review>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="text-sm font-semibold">Email Draft</h4>
          <span className="text-xs text-zinc-500">Edit only if needed, then approve all drafts. Footers are added automatically.</span>
        </div>
        {visibleDrafts.map((draft) => {
          const edit = draftEdits[draft.id] || { subject: draft.subject, body: draft.body };
          const missingFooter = draftMissingRequiredFooter(edit.body, settingsForm.unsubscribe_text || '');
          return (
            <div className="rounded border border-zinc-800 p-2" key={draft.id}>
              <div className="mb-2 flex flex-wrap justify-between gap-2 text-xs"><strong>{draft.business || draft.lead_email || draft.lead_key}</strong><span>{draft.status}</span></div>
              {missingFooter ? <div className="mb-2 rounded border border-amber-900 bg-amber-950/20 p-2 text-xs text-amber-200">Missing unsubscribe footer. Saving or approving will add it automatically.</div> : null}
              <input className="input mb-2" value={edit.subject} onChange={(event) => setDraftEdits({ ...draftEdits, [draft.id]: { ...edit, subject: event.target.value } })} />
              <textarea className="input min-h-36 text-xs" value={edit.body} onChange={(event) => setDraftEdits({ ...draftEdits, [draft.id]: { ...edit, body: event.target.value } })} />
              <div className="mt-2 flex flex-wrap gap-2">
                <button className="btn-secondary text-xs" type="button" disabled={busy === `save-draft:${draft.id}`} onClick={() => saveDraft(draft)}>Save draft changes</button>
                <button className="btn-secondary text-xs" type="button" disabled={busy === `approve-draft:${draft.id}` || draft.status === 'draft_approved'} onClick={() => approveDraft(draft)}>Approve this draft</button>
              </div>
            </div>
          );
        })}
        {!visibleDrafts.length ? <p className="text-xs text-zinc-500">No email draft yet. Approve leads, then generate an email draft.</p> : null}
      </section> : null}

      {showEmailWorkflow ? <details className="rounded border border-zinc-800 p-3" data-voryx-email-advanced>
        <summary className="cursor-pointer text-sm font-semibold">Advanced email settings and Hermes safety</summary>
        <div className="mt-3 grid gap-3">
          <div className="grid gap-2 md:grid-cols-3">
            <input className="input" placeholder="Sender name" value={settingsForm.sender_name || ''} onChange={(e) => setSettings({ ...settingsForm, sender_name: e.target.value })} />
            <input className="input" placeholder="Sender email" value={settingsForm.sender_email || ''} onChange={(e) => setSettings({ ...settingsForm, sender_email: e.target.value })} />
            <input className="input" placeholder="Reply-to email" value={settingsForm.reply_to_email || ''} onChange={(e) => setSettings({ ...settingsForm, reply_to_email: e.target.value })} />
            <input className="input" placeholder="Physical mailing address" value={settingsForm.physical_mailing_address || ''} onChange={(e) => setSettings({ ...settingsForm, physical_mailing_address: e.target.value })} />
            <input className="input" placeholder="Unsubscribe text" value={settingsForm.unsubscribe_text || ''} onChange={(e) => setSettings({ ...settingsForm, unsubscribe_text: e.target.value })} />
            <input className="input" type="number" min="1" max="5" placeholder="Daily limit" value={settingsForm.daily_send_limit || 5} onChange={(e) => setSettings({ ...settingsForm, daily_send_limit: Number(e.target.value || 5) })} />
            <input className="input" type="number" min="1" max="5" placeholder="Hourly limit" value={settingsForm.hourly_send_limit || 1} onChange={(e) => setSettings({ ...settingsForm, hourly_send_limit: Number(e.target.value || 1) })} />
          </div>
          <div className="grid gap-3 rounded border border-zinc-800 p-3 text-xs text-zinc-300" data-voryx-approved-sending-window>
            <div>
              <div className="mb-2 font-medium text-zinc-200">Approved sending window</div>
              <div className="flex flex-wrap gap-2">
                {WEEKDAYS.map((day) => (
                  <label className="flex items-center gap-1 rounded border border-zinc-800 px-2 py-1" key={day}>
                    <input type="checkbox" checked={allowedDays.includes(day)} onChange={(event) => updateAllowedDay(day, event.target.checked)} />
                    {day.slice(0, 3)}
                  </label>
                ))}
              </div>
              <p className="mt-1 text-zinc-500">No selected days means every day is allowed.</p>
            </div>
            <div className="grid gap-2 md:grid-cols-5">
              <label className="grid gap-1">
                <span>Start time</span>
                <input className="input" type="time" value={allowedHours.start || '09:00'} onChange={(event) => updateAllowedHour('start', event.target.value)} />
              </label>
              <label className="grid gap-1">
                <span>End time</span>
                <input className="input" type="time" value={allowedHours.end || '17:00'} onChange={(event) => updateAllowedHour('end', event.target.value)} />
              </label>
              <label className="grid gap-1">
                <span>Start date</span>
                <input className="input" type="date" value={settingsForm.allowed_sending_start_date || ''} onChange={(event) => setSettings({ ...settingsForm, allowed_sending_start_date: event.target.value || null })} />
              </label>
              <label className="grid gap-1">
                <span>End date</span>
                <input className="input" type="date" value={settingsForm.allowed_sending_end_date || ''} onChange={(event) => setSettings({ ...settingsForm, allowed_sending_end_date: event.target.value || null })} />
              </label>
              <label className="grid gap-1">
                <span>Timezone</span>
                <input className="input" value={settingsForm.timezone || 'America/Toronto'} onChange={(event) => setSettings({ ...settingsForm, timezone: event.target.value })} />
              </label>
            </div>
          </div>
          <div className="flex flex-wrap gap-3 text-xs text-zinc-300">
            <label><input type="checkbox" checked={Boolean(settingsForm.compliance_acknowledged)} onChange={(e) => setSettings({ ...settingsForm, compliance_acknowledged: e.target.checked })} /> Compliance acknowledged</label>
            <button className="btn-secondary text-xs" type="button" disabled={busy === 'settings'} onClick={() => updateSettings(settingsForm)}>Save email settings</button>
            <button className="btn-secondary text-xs" type="button" disabled={busy === 'enable-prospect' || settingsForm.prospect_sending_enabled} onClick={() => setProspectSending(true)}>Enable sending</button>
            <button className="btn-secondary text-xs" type="button" disabled={busy === 'disable-prospect' || !settingsForm.prospect_sending_enabled} onClick={() => setProspectSending(false)}>Disable sending</button>
          </div>
          <div className="grid gap-1 rounded border border-zinc-800 p-2 text-xs text-zinc-400" data-voryx-sender-verification>
            <div>Sender verification: <span className={settingsForm.sender_verification?.verified ? 'text-emerald-300' : 'text-amber-300'}>{settingsForm.sender_verification?.verified ? 'Verified' : 'Not verified'}</span></div>
            <div>Account: <span className="text-zinc-200">{settingsForm.sender_verification?.sender_email || '-'}</span></div>
            <div>Allowed window: {formatWindow(windowInfo)} / Current time: {windowInfo.local_now || '-'}</div>
            <div>Real send confirmation: SEND CONTROLLED BATCH</div>
            <div>Hermes/Himalaya receipt required before any email counts as sent.</div>
          </div>
          <div className="grid gap-1 text-xs text-zinc-500">
            <div>Follow-up: disabled until reply monitor is connected. This belongs to a separate follow-up employee.</div>
            <div>Cold calling, text marketing and social outreach are separate employees and are not part of this email marketing workflow.</div>
            <div>Prospect emails sent during QA actions: 0</div>
          </div>
        </div>
      </details> : null}
    </div>
  );
}
