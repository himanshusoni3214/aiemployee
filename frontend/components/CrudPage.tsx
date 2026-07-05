'use client';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';
import { isSafetyLockedHermesJob } from '../lib/hermesSafety';
import { ManualRunUnavailable, defaultConnectorCapabilities, type ConnectorCapabilities } from './ActionButtons';

type Option = { value: string; label: string };
type FieldConfig = {
  label?: string;
  type?: 'text' | 'number' | 'textarea' | 'boolean' | 'json' | 'select' | 'readonly' | 'days' | 'hours' | 'date';
  options?: Option[];
  hidden?: boolean;
  readOnly?: boolean;
};

const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

function parseValue(sample: unknown, value: string) {
  if (typeof sample === 'number') return Number(value || 0);
  if (typeof sample === 'boolean') return value === 'true';
  if (sample && typeof sample === 'object') {
    try { return JSON.parse(value || '{}'); } catch { return sample; }
  }
  return value;
}

function withQuery(path: string, query?: Record<string, string | undefined>) {
  const params = new URLSearchParams();
  Object.entries(query || {}).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return `${path}${params.toString() ? `?${params.toString()}` : ''}`;
}

function itemTitle(item: any) {
  return item.name || item.email || item.id;
}

function itemMeta(item: any, maps?: Record<string, Record<string, string>>) {
  const parts = [];
  if (item.company_id) parts.push(maps?.company_id?.[item.company_id] || item.company_id);
  if (item.campaign_id) parts.push(maps?.campaign_id?.[item.campaign_id] || item.campaign_id);
  if (item.employee_type) parts.push(item.employee_type);
  if (item.task_type) parts.push(item.task_type);
  if (
    Object.prototype.hasOwnProperty.call(item, 'daily_lead_goal') ||
    Object.prototype.hasOwnProperty.call(item, 'daily_email_goal')
  ) {
    parts.push(
      `${Number(item.daily_lead_goal ?? 0)} leads, ${Number(item.daily_email_goal ?? 0)} emails`,
    );
  }
  if (item.status) parts.push(item.status);
  if (item.campaign_type && item.campaign_type !== 'custom') parts.push(item.campaign_type.replaceAll('_', ' '));
  if (item.provisioning_state) parts.push(item.provisioning_state);
  return parts.join(' / ');
}

export default function CrudPage({
  title,
  path,
  defaults,
  initialItems = [],
  query,
  fields = {},
  createLabel,
  emptyLabel,
  displayMaps,
  capabilities,
}: {
  title: string;
  path: string;
  defaults: Record<string, unknown>;
  initialItems?: any[];
  query?: Record<string, string | undefined>;
  fields?: Record<string, FieldConfig>;
  createLabel?: string;
  emptyLabel?: string;
  displayMaps?: Record<string, Record<string, string>>;
  capabilities?: ConnectorCapabilities | null;
}) {
  const caps = { ...defaultConnectorCapabilities, ...(capabilities || {}) };
  const listPath = useMemo(() => withQuery(path, query), [path, JSON.stringify(query || {})]);
  const [items, setItems] = useState<any[]>(initialItems);
  const [form, setForm] = useState(defaults);
  const [editingId, setEditingId] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState('');

  useEffect(() => {
    setItems(initialItems);
    setForm(defaults);
    setEditingId('');
  }, [JSON.stringify(initialItems), JSON.stringify(defaults)]);

  async function load() {
    setLoading(true);
    try {
      const data = await api(listPath);
      setItems(Array.isArray(data) ? data : []);
      setError('');
    } catch (err: any) {
      setError(err?.message || 'API failure while loading records');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [listPath]);

  async function save() {
    try {
      setBusy(editingId ? 'save' : 'create');
      await api(editingId ? `${path}/${editingId}` : path, { method: editingId ? 'PUT' : 'POST', body: JSON.stringify(form) });
      setForm(defaults);
      setEditingId('');
      setMessage(editingId ? 'Save succeeded' : 'Create succeeded');
      setError('');
      await load();
    } catch (err: any) {
      setError(err.message || 'Request failed');
      setMessage('');
    } finally {
      setBusy('');
    }
  }

  async function archive(item: any) {
    try {
      setBusy(item.id);
      if (item.status === 'Archived') {
        const restoreForm = { ...defaults };
        for (const key of Object.keys(defaults)) restoreForm[key] = item[key] ?? defaults[key];
        await api(`${path}/${item.id}`, { method: 'PUT', body: JSON.stringify({ ...restoreForm, status: path === '/employees' ? 'Stopped' : 'Active' }) });
        setMessage('Restore succeeded');
      } else {
        await api(`${path}/${item.id}`, { method: 'DELETE' });
        setMessage('Archive succeeded');
      }
      setError('');
      await load();
    } catch (err: any) {
      setError(err.message || 'Request failed');
      setMessage('');
    } finally {
      setBusy('');
    }
  }

  async function postAction(item: any, action: string) {
    try {
      setBusy(`${item.id}:${action}`);
      await api(`${path}/${item.id}/${action}`, { method: 'POST' });
      setMessage(`${action.replace('-', ' ')} request accepted`);
      setError('');
      await load();
    } catch (err: any) {
      setError(err.message || 'Action failed');
      setMessage('');
    } finally {
      setBusy('');
    }
  }

  async function postPath(actionPath: string, label: string) {
    try {
      setBusy(actionPath);
      const result = await api(actionPath, { method: 'POST' });
      setMessage(result?.message || `${label} completed`);
      setError('');
      await load();
    } catch (err: any) {
      setError(err.message || `${label} failed`);
      setMessage('');
    } finally {
      setBusy('');
    }
  }

  function edit(item: any) {
    const next = { ...defaults };
    for (const key of Object.keys(defaults)) next[key] = item[key] ?? defaults[key];
    setForm(next);
    setEditingId(item.id);
    setMessage(`Editing ${itemTitle(item)}`);
    setError('');
  }

  function cancelEdit() {
    setEditingId('');
    setForm(defaults);
    setMessage('');
    setError('');
  }

  function itemHermesJobId(item: any) {
    const payload = item.payload && typeof item.payload === 'object' ? item.payload : {};
    return item.hermes_job_id || payload.hermes_job_id || null;
  }

  function campaignTemplateAction(item: any) {
    const type = String(item.campaign_type || 'custom');
    const employees = Array.isArray(item?.provisioning_result?.employees) ? item.provisioning_result.employees : [];
    if (type === 'lead_research' || employees.some((entry: any) => entry?.employee_template === 'lead_researcher')) return { action: 'generate-sample', label: 'Generate sample' };
    if (type === 'daily_reporting' || employees.some((entry: any) => entry?.employee_template === 'daily_reporter')) return { action: 'send-internal-test', label: 'Send internal test' };
    if (type === 'outreach_drafting' || employees.some((entry: any) => entry?.employee_template === 'outreach_draft_writer')) return { action: 'generate-sample-draft', label: 'Generate sample draft' };
    return null;
  }

  function canShowManualRun(item: any) {
    return Boolean(caps.supports_manual_run) && !isSafetyLockedHermesJob(itemHermesJobId(item));
  }

  function canShowDryRun(item: any) {
    return Boolean(caps.supports_dry_run) && !isSafetyLockedHermesJob(itemHermesJobId(item));
  }

  function renderField(key: string) {
    const config = fields[key] || {};
    if (config.hidden) return null;
    const sample = defaults[key];
    const value = form[key];
    const type = config.type || (typeof sample === 'number' ? 'number' : typeof sample === 'boolean' ? 'boolean' : sample && typeof sample === 'object' ? 'json' : 'text');
    const label = config.label || key.replaceAll('_', ' ');
    const disabled = config.readOnly || type === 'readonly';
    const fieldId = `crud-${path.replace(/[^a-z0-9]+/gi, '-')}-${key}`;
    return (
      <div key={key} className="grid gap-1 text-sm text-zinc-300" data-voryx-crud-field-wrapper={key}>
        {type !== 'days' && type !== 'hours' ? <label htmlFor={fieldId}>{label}</label> : null}
        {type === 'select' ? (
          <select id={fieldId} className="input" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={String(value ?? '')} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })}>
            <option value="">Select</option>
            {(config.options || []).map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
          </select>
        ) : type === 'boolean' ? (
          <select id={fieldId} className="input" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={String(value)} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })}>
            <option value="false">false</option>
            <option value="true">true</option>
          </select>
        ) : type === 'textarea' ? (
          <textarea id={fieldId} className="input min-h-24" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={String(value ?? '')} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
        ) : type === 'json' ? (
          <textarea id={fieldId} className="input min-h-24 font-mono text-xs" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={JSON.stringify(value ?? {}, null, 2)} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })} />
        ) : type === 'days' ? (
          <fieldset className="rounded border border-zinc-800 p-2" data-voryx-crud-field={key} data-voryx-crud-type={type}>
            <legend className="px-1 text-sm text-zinc-300">{label}</legend>
            <div className="flex flex-wrap gap-2">
            {weekdays.map((day) => {
              const values = Array.isArray(value) ? value : [];
              const dayId = `${fieldId}-${day.toLowerCase()}`;
              return <div className="flex items-center gap-1 text-xs" key={day}><input id={dayId} name={key} value={day} type="checkbox" checked={values.includes(day)} onChange={(event) => setForm({ ...form, [key]: event.target.checked ? [...values, day] : values.filter((item) => item !== day) })} /><label htmlFor={dayId}>{day.slice(0, 3)}</label></div>;
            })}
            </div>
          </fieldset>
        ) : type === 'hours' ? (
          <fieldset className="grid gap-2" data-voryx-crud-field={key} data-voryx-crud-type={type}>
            <legend className="text-sm text-zinc-300">{label}</legend>
            <div className="grid grid-cols-2 gap-2">
              <div className="grid gap-1"><label htmlFor={`${fieldId}-start`} className="text-xs text-zinc-400">Start</label><input id={`${fieldId}-start`} className="input" name={`${key}.start`} data-voryx-crud-hour="start" type="time" value={String((value as any)?.start || '09:00')} onChange={(event) => setForm({ ...form, [key]: { ...(typeof value === 'object' && value ? value : {}), start: event.target.value } })} /></div>
              <div className="grid gap-1"><label htmlFor={`${fieldId}-end`} className="text-xs text-zinc-400">End</label><input id={`${fieldId}-end`} className="input" name={`${key}.end`} data-voryx-crud-hour="end" type="time" value={String((value as any)?.end || '17:00')} onChange={(event) => setForm({ ...form, [key]: { ...(typeof value === 'object' && value ? value : {}), end: event.target.value } })} /></div>
            </div>
          </fieldset>
        ) : (
          <input id={fieldId} className="input" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} type={type === 'number' ? 'number' : type === 'date' ? 'date' : 'text'} value={String(value ?? '')} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })} />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5" data-voryx-crud-page data-voryx-crud-path={path} data-voryx-crud-defaults={JSON.stringify(defaults)}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xl font-semibold">{title}</h2>
        <div className="text-sm text-zinc-400">{loading ? 'Loading' : `${items.length} records`}</div>
      </div>
      <div className="card">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Object.keys(defaults).map(renderField)}
        </div>
        {message ? <p className="mt-3 text-sm text-emerald-300">{message}</p> : null}
        {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
        <p className="mt-3 text-sm text-emerald-300" hidden data-voryx-crud-message />
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn" type="button" disabled={Boolean(busy)} data-voryx-crud-save onClick={save}>{editingId ? 'Save Changes' : createLabel || 'Create'}</button>
          <button className="btn-secondary" type="button" disabled={Boolean(busy)} hidden={!editingId} data-voryx-crud-cancel onClick={cancelEdit}>Cancel</button>
        </div>
      </div>
      <div className="overflow-hidden border border-zinc-800">
        {items.map((item) => (
          <div className="border-b border-zinc-800 bg-zinc-950/60 p-4 last:border-b-0" key={item.id} data-voryx-crud-row data-voryx-crud-item={JSON.stringify(item)}>
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-medium text-stone-100">{itemTitle(item)}</p>
                <p className="text-xs text-zinc-500">{itemMeta(item, displayMaps) || item.id}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className="btn-secondary text-xs" type="button" data-voryx-crud-edit onClick={() => edit(item)}>Edit</button>
                {path === '/campaigns' ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label="duplicate" data-voryx-action-path={`${path}/${item.id}/duplicate`} onClick={() => postAction(item, 'duplicate')}>Duplicate</button> : null}
                {path === '/campaigns' && item.status !== 'Archived' ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label={item.status === 'Inactive' ? 'resume' : 'pause'} data-voryx-action-path={`${path}/${item.id}/${item.status === 'Inactive' ? 'resume' : 'pause'}`} onClick={() => postAction(item, item.status === 'Inactive' ? 'resume' : 'pause')}>{item.status === 'Inactive' ? 'Resume' : 'Pause'}</button> : null}
                {path === '/campaigns' && campaignTemplateAction(item) ? (
                  <button
                    className="btn-secondary text-xs"
                    type="button"
                    data-voryx-action-label={campaignTemplateAction(item)?.action}
                    data-voryx-action-path={`${path}/${item.id}/template/${campaignTemplateAction(item)?.action}`}
                    onClick={() => {
                      const templateAction = campaignTemplateAction(item);
                      if (templateAction) void postPath(`${path}/${item.id}/template/${templateAction.action}`, templateAction.label);
                    }}
                  >
                    {campaignTemplateAction(item)?.label}
                  </button>
                ) : null}
                {path === '/employees' && item.status !== 'Archived' && isSafetyLockedHermesJob(itemHermesJobId(item)) ? <span className="rounded border border-amber-700 px-2 py-1 text-xs text-amber-300" title="Safety blocked: this worker can send real Gmail prospect outreach.">Locked</span> : null}
                {path === '/employees' && item.status !== 'Archived' && !isSafetyLockedHermesJob(itemHermesJobId(item)) && ['Running', 'Scheduled'].includes(item.status) ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label="pause" data-voryx-action-path={`${path}/${item.id}/pause`} onClick={() => postAction(item, 'pause')}>Pause</button> : null}
                {path === '/employees' && item.status !== 'Archived' && !isSafetyLockedHermesJob(itemHermesJobId(item)) && ['Paused', 'Stopped'].includes(item.status) ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label="resume" data-voryx-action-path={`${path}/${item.id}/resume`} onClick={() => postAction(item, 'resume')}>Resume</button> : null}
                {path === '/employees' && item.status === 'Scheduled' && canShowManualRun(item) ? <button className="btn text-xs" type="button" data-voryx-action-label="run" data-voryx-action-path={`${path}/${item.id}/run`} onClick={() => postAction(item, 'run')}>Run</button> : null}
                {path === '/employees' && item.status === 'Scheduled' && canShowDryRun(item) ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label="dry-run" data-voryx-action-path={`${path}/${item.id}/dry-run`} onClick={() => postAction(item, 'dry-run')}>Dry Run</button> : null}
                {path === '/schedules' && item.status !== 'Archived' && isSafetyLockedHermesJob(itemHermesJobId(item)) ? <span className="rounded border border-amber-700 px-2 py-1 text-xs text-amber-300" title="Safety blocked: this schedule can send real Gmail prospect outreach.">Locked</span> : null}
                {path === '/schedules' && item.status !== 'Archived' && !isSafetyLockedHermesJob(itemHermesJobId(item)) ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label={item.is_paused ? 'resume' : 'pause'} data-voryx-action-path={`${path}/${item.id}/${item.is_paused ? 'resume' : 'pause'}`} onClick={() => postAction(item, item.is_paused ? 'resume' : 'pause')}>{item.is_paused ? 'Resume' : 'Pause'}</button> : null}
                {path === '/schedules' && item.status !== 'Archived' && !item.is_paused && canShowManualRun(item) ? <button className="btn text-xs" type="button" data-voryx-action-label="run" data-voryx-action-path={`${path}/${item.id}/run`} onClick={() => postAction(item, 'run')}>Run Now</button> : null}
                {path === '/schedules' && item.status !== 'Archived' && !item.is_paused && canShowDryRun(item) ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label="dry-run" data-voryx-action-path={`${path}/${item.id}/dry-run`} onClick={() => postAction(item, 'dry-run')}>Dry Run</button> : null}
                {path === '/schedules' && item.status !== 'Archived' && !item.is_paused && canShowDryRun(item) ? <button className="btn-secondary text-xs" type="button" data-voryx-action-label="test-run" data-voryx-action-path={`${path}/${item.id}/test-run`} onClick={() => postAction(item, 'test-run')}>Test Run</button> : null}
                {path === '/employees' && item.status === 'Scheduled' && !isSafetyLockedHermesJob(itemHermesJobId(item)) && !caps.supports_manual_run && !caps.supports_dry_run ? <ManualRunUnavailable capabilities={caps} /> : null}
                {path === '/schedules' && item.status !== 'Archived' && !item.is_paused && !isSafetyLockedHermesJob(itemHermesJobId(item)) && !caps.supports_manual_run && !caps.supports_dry_run ? <ManualRunUnavailable capabilities={caps} /> : null}
                {path !== '/schedules' ? <button className="btn-secondary text-xs" type="button" data-voryx-crud-archive onClick={() => archive(item)}>{item.status === 'Archived' ? 'Restore' : 'Archive'}</button> : null}
              </div>
            </div>
            <pre className="max-h-64 overflow-auto text-xs text-zinc-300">{JSON.stringify(item, null, 2)}</pre>
          </div>
        ))}
        {!items.length ? <div className="p-5 text-sm text-zinc-400">{loading ? 'Loading records' : emptyLabel || 'No records for selected company'}</div> : null}
      </div>
    </div>
  );
}
