'use client';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type ReviewItem = { lead_key: string; business?: string; email?: string; domain?: string; state: string; computed_state: string; reason?: string; can_send: boolean };
type Draft = { id: string; lead_key: string; lead_email?: string; business?: string; subject: string; body: string; status: string };

export function OutreachControlsPanel({ companyId, campaignId }: { companyId: string; campaignId: string }) {
  const [settings, setSettings] = useState<any>(null);
  const [review, setReview] = useState<{ items: ReviewItem[]; counts: Record<string, number>; eligible_count: number; source_path?: string } | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [sendStatus, setSendStatus] = useState<any>(null);
  const [followups, setFollowups] = useState<any>(null);
  const [replyMonitor, setReplyMonitor] = useState<any>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  async function load() {
    try {
      const [settingsData, reviewData, draftData, sendData, followData, replyData] = await Promise.all([
        api(`/companies/${companyId}/outreach-settings`),
        api(`/campaigns/${campaignId}/lead-review`),
        api(`/campaigns/${campaignId}/outreach-drafts`),
        api(`/campaigns/${campaignId}/outreach-send/status`),
        api(`/campaigns/${campaignId}/followups/status`),
        api(`/campaigns/${campaignId}/reply-monitor/status`),
      ]);
      setSettings(settingsData);
      setReview(reviewData);
      setDrafts(draftData.drafts || []);
      setSendStatus(sendData);
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

  const blockers = settings?.blocking_reasons || [];
  const senderReady = settings?.ready_for_prospect_sending;
  const settingsForm = settings || {};

  return (
    <div className="mt-3 grid gap-3 rounded border border-zinc-800 p-3" data-voryx-outreach-controls>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-zinc-100">Outreach Control</h3>
          <p className="text-xs text-zinc-500">Approval-based. Drafts only until compliance, sender, reply monitoring and limits pass.</p>
        </div>
        <div className={senderReady ? 'text-xs text-emerald-400' : 'text-xs text-amber-300'}>{senderReady ? 'Ready' : `Blocked: ${blockers.join(', ') || 'settings incomplete'}`}</div>
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
      <div className="flex flex-wrap gap-3 text-xs text-zinc-300">
        <label><input type="checkbox" checked={Boolean(settingsForm.approved_sender_connected)} onChange={(e) => setSettings({ ...settingsForm, approved_sender_connected: e.target.checked })} /> Approved sender connected</label>
        <label><input type="checkbox" checked={Boolean(settingsForm.compliance_acknowledged)} onChange={(e) => setSettings({ ...settingsForm, compliance_acknowledged: e.target.checked })} /> Compliance acknowledged</label>
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'settings'} onClick={() => updateSettings(settingsForm)}>Save settings</button>
      </div>

      <div className="grid gap-2 text-xs text-zinc-400">
        <div>Lead source: <span className="text-zinc-200">{review?.source_path || 'No CSV source found'}</span></div>
        <div>Lead queue: {Object.entries(review?.counts || {}).map(([k, v]) => `${k}=${v}`).join(' / ') || '0'} / eligible={review?.eligible_count || 0}</div>
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

      <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
        <button className="btn-secondary text-xs" type="button" disabled={busy === 'generate-drafts'} onClick={generateDrafts}>Generate drafts for approved leads</button>
        <span>Prospect sending stays blocked unless all settings and approvals pass.</span>
      </div>
      <div className="grid gap-2">
        {drafts.slice(0, 10).map((draft) => <div className="rounded border border-zinc-800 p-2" key={draft.id}>
          <div className="flex flex-wrap justify-between gap-2 text-xs"><strong>{draft.subject}</strong><span>{draft.status}</span></div>
          <pre className="mt-2 whitespace-pre-wrap text-xs text-zinc-400">{draft.body}</pre>
          <div className="mt-2 flex flex-wrap gap-2">
            <button className="btn-secondary text-xs" type="button" onClick={() => draftAction(draft, 'approve')}>Approve draft</button>
            <button className="btn-secondary text-xs" type="button" onClick={() => draftAction(draft, 'reject')}>Reject draft</button>
            <button className="btn-secondary text-xs" type="button" disabled={draft.status !== 'draft_approved'} onClick={() => draftAction(draft, 'internal-test')}>Internal test</button>
          </div>
        </div>)}
      </div>
      <div className="grid gap-1 text-xs text-zinc-500">
        <div>Prospect send blockers: {(sendStatus?.prospect_send_blockers || []).join(', ') || 'none'}</div>
        <div>Follow-up: {followups?.state || 'unknown'}{followups?.reason ? ` / ${followups.reason}` : ''}</div>
        <div>Reply Monitor: {replyMonitor?.state || 'unknown'}{replyMonitor?.reason ? ` / ${replyMonitor.reason}` : ''}</div>
      </div>
    </div>
  );
}
