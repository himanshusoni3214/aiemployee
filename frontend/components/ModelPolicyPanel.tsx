'use client';

import { useEffect, useState } from 'react';
import { api } from '../lib/api';

type Scope = 'global' | 'company' | 'employee';

type Props = {
  scope: Scope;
  companyId?: string;
  employeeId?: string;
  title?: string;
  compact?: boolean;
};

function endpoint(props: Props) {
  if (props.scope === 'company') return `/companies/${props.companyId}/model-policy`;
  if (props.scope === 'employee') return `/employees/${props.employeeId}/model-policy`;
  return '/model-policy/global';
}

function listText(value: unknown) {
  return Array.isArray(value) ? value.join('\n') : '';
}

function parseList(value: string) {
  return value.split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

export function ModelPolicyPanel(props: Props) {
  const [policy, setPolicy] = useState<any>(null);
  const [form, setForm] = useState<any>({});
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function load() {
    if ((props.scope === 'company' && !props.companyId) || (props.scope === 'employee' && !props.employeeId)) return;
    try {
      const data = await api(endpoint(props));
      setPolicy(data);
      const effective = data.effective || data;
      setForm({
        provider: data.provider || effective.provider || 'openrouter',
        model: data.model || effective.model || 'nvidia/nemotron-3-super-120b-a12b',
        approved_models: listText(data.approved_models?.length ? data.approved_models : effective.approved_models),
        blocked_models: listText(data.blocked_models?.length ? data.blocked_models : effective.blocked_models),
        fail_closed: data.fail_closed ?? effective.fail_closed ?? true,
        fallback_enabled: false,
        daily_budget_usd: data.daily_budget_usd ?? effective.daily_budget_usd ?? 0,
        monthly_budget_usd: data.monthly_budget_usd ?? effective.monthly_budget_usd ?? 0,
        max_cost_per_run_usd: data.max_cost_per_run_usd ?? effective.max_cost_per_run_usd ?? 0,
        notes: data.notes || '',
      });
      setError('');
    } catch (err: any) {
      setError(err.message || 'Model policy failed to load');
    }
  }

  useEffect(() => { load(); }, [props.scope, props.companyId, props.employeeId]);

  async function save() {
    setBusy(true);
    try {
      const result = await api(endpoint(props), {
        method: 'PUT',
        body: JSON.stringify({
          ...form,
          approved_models: parseList(form.approved_models || ''),
          blocked_models: parseList(form.blocked_models || ''),
          fallback_enabled: false,
          daily_budget_usd: Number(form.daily_budget_usd || 0),
          monthly_budget_usd: Number(form.monthly_budget_usd || 0),
          max_cost_per_run_usd: Number(form.max_cost_per_run_usd || 0),
        }),
      });
      setPolicy(result);
      setMessage('Model policy saved and synced to Hermes jobs.json');
      setError('');
      await load();
    } catch (err: any) {
      setError(err.message || 'Model policy save failed');
      setMessage('');
    } finally {
      setBusy(false);
    }
  }

  const effective = policy?.effective || policy || {};
  if ((props.scope === 'company' && !props.companyId) || (props.scope === 'employee' && !props.employeeId)) return null;

  return (
    <section className="card" data-voryx-model-policy-panel data-voryx-model-policy-scope={props.scope}>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">{props.title || 'Model Policy'}</h2>
          <p className="text-sm text-zinc-400">Effective model: {effective.normalized_model || 'loading'} / fallback disabled</p>
        </div>
        <div className="rounded border border-emerald-900 px-2 py-1 text-xs text-emerald-300">Fail closed</div>
      </div>
      {error ? <div className="mb-3 rounded border border-red-900 bg-red-950/40 p-2 text-xs text-red-200">{error}</div> : null}
      {message ? <div className="mb-3 rounded border border-emerald-900 bg-emerald-950/30 p-2 text-xs text-emerald-200">{message}</div> : null}
      <div className="grid gap-3 md:grid-cols-2">
        <label className="grid gap-1 text-xs text-zinc-400">Provider<input className="input" value={form.provider || ''} onChange={(event) => setForm({ ...form, provider: event.target.value })} /></label>
        <label className="grid gap-1 text-xs text-zinc-400">Model<input className="input" value={form.model || ''} onChange={(event) => setForm({ ...form, model: event.target.value })} /></label>
        <label className="grid gap-1 text-xs text-zinc-400">Approved models<textarea className="input min-h-24" value={form.approved_models || ''} onChange={(event) => setForm({ ...form, approved_models: event.target.value })} /></label>
        <label className="grid gap-1 text-xs text-zinc-400">Blocked models<textarea className="input min-h-24" value={form.blocked_models || ''} onChange={(event) => setForm({ ...form, blocked_models: event.target.value })} /></label>
        <label className="grid gap-1 text-xs text-zinc-400">Max cost per run USD<input className="input" type="number" min="0" value={form.max_cost_per_run_usd || 0} onChange={(event) => setForm({ ...form, max_cost_per_run_usd: Number(event.target.value || 0) })} /></label>
        <label className="grid gap-1 text-xs text-zinc-400">Daily budget USD<input className="input" type="number" min="0" value={form.daily_budget_usd || 0} onChange={(event) => setForm({ ...form, daily_budget_usd: Number(event.target.value || 0) })} /></label>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-zinc-400">
        <label><input type="checkbox" checked={Boolean(form.fail_closed)} onChange={(event) => setForm({ ...form, fail_closed: event.target.checked })} /> Fail closed</label>
        <label><input type="checkbox" checked={false} disabled readOnly /> Silent fallback disabled</label>
        <button className="btn-secondary text-xs" type="button" disabled={busy} onClick={save}>Save model policy</button>
      </div>
    </section>
  );
}
