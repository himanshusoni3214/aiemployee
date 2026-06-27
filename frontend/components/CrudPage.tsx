'use client';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../lib/api';

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
  if (item.status) parts.push(item.status);
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
}) {
  const listPath = useMemo(() => withQuery(path, query), [path, JSON.stringify(query || {})]);
  const [items, setItems] = useState<any[]>(initialItems);
  const [form, setForm] = useState(defaults);
  const [editingId, setEditingId] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

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
      await api(editingId ? `${path}/${editingId}` : path, { method: editingId ? 'PUT' : 'POST', body: JSON.stringify(form) });
      setForm(defaults);
      setEditingId('');
      await load();
    } catch (err: any) {
      setError(err.message || 'Request failed');
    }
  }

  async function archive(item: any) {
    try {
      if (item.status === 'Archived') {
        const restoreForm = { ...defaults };
        for (const key of Object.keys(defaults)) restoreForm[key] = item[key] ?? defaults[key];
        await api(`${path}/${item.id}`, { method: 'PUT', body: JSON.stringify({ ...restoreForm, status: path === '/employees' ? 'Stopped' : 'Active' }) });
      } else {
        await api(`${path}/${item.id}`, { method: 'DELETE' });
      }
      await load();
    } catch (err: any) {
      setError(err.message || 'Request failed');
    }
  }

  async function postAction(item: any, action: string) {
    try {
      await api(`${path}/${item.id}/${action}`, { method: 'POST' });
      await load();
    } catch (err: any) {
      setError(err.message || 'Action failed');
    }
  }

  function edit(item: any) {
    const next = { ...defaults };
    for (const key of Object.keys(defaults)) next[key] = item[key] ?? defaults[key];
    setForm(next);
    setEditingId(item.id);
  }

  function renderField(key: string) {
    const config = fields[key] || {};
    if (config.hidden) return null;
    const sample = defaults[key];
    const value = form[key];
    const type = config.type || (typeof sample === 'number' ? 'number' : typeof sample === 'boolean' ? 'boolean' : sample && typeof sample === 'object' ? 'json' : 'text');
    const label = config.label || key.replaceAll('_', ' ');
    const disabled = config.readOnly || type === 'readonly';
    return (
      <label key={key} className="grid gap-1 text-sm text-zinc-300" data-voryx-crud-field-wrapper={key}>
        <span>{label}</span>
        {type === 'select' ? (
          <select className="input" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={String(value ?? '')} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })}>
            <option value="">Select</option>
            {(config.options || []).map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
          </select>
        ) : type === 'boolean' ? (
          <select className="input" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={String(value)} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })}>
            <option value="false">false</option>
            <option value="true">true</option>
          </select>
        ) : type === 'textarea' ? (
          <textarea className="input min-h-24" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={String(value ?? '')} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
        ) : type === 'json' ? (
          <textarea className="input min-h-24 font-mono text-xs" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} value={JSON.stringify(value ?? {}, null, 2)} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })} />
        ) : type === 'days' ? (
          <div className="flex flex-wrap gap-2 rounded border border-zinc-800 p-2" data-voryx-crud-field={key} data-voryx-crud-type={type}>
            {weekdays.map((day) => {
              const values = Array.isArray(value) ? value : [];
              return <label className="flex items-center gap-1 text-xs" key={day}><input name={key} value={day} type="checkbox" checked={values.includes(day)} onChange={(event) => setForm({ ...form, [key]: event.target.checked ? [...values, day] : values.filter((item) => item !== day) })} />{day.slice(0, 3)}</label>;
            })}
          </div>
        ) : type === 'hours' ? (
          <div className="grid grid-cols-2 gap-2" data-voryx-crud-field={key} data-voryx-crud-type={type}>
            <input className="input" name={`${key}.start`} data-voryx-crud-hour="start" type="time" value={String((value as any)?.start || '09:00')} onChange={(event) => setForm({ ...form, [key]: { ...(typeof value === 'object' && value ? value : {}), start: event.target.value } })} />
            <input className="input" name={`${key}.end`} data-voryx-crud-hour="end" type="time" value={String((value as any)?.end || '17:00')} onChange={(event) => setForm({ ...form, [key]: { ...(typeof value === 'object' && value ? value : {}), end: event.target.value } })} />
          </div>
        ) : (
          <input className="input" name={key} data-voryx-crud-field={key} data-voryx-crud-type={type} disabled={disabled} type={type === 'number' ? 'number' : type === 'date' ? 'date' : 'text'} value={String(value ?? '')} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })} />
        )}
      </label>
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
        {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
        <p className="mt-3 text-sm text-emerald-300" hidden data-voryx-crud-message />
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn" type="button" data-voryx-crud-save onClick={save}>{editingId ? 'Save Changes' : createLabel || 'Create'}</button>
          <button className="btn-secondary" type="button" hidden={!editingId} data-voryx-crud-cancel onClick={() => { setEditingId(''); setForm(defaults); }}>Cancel</button>
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
                {path === '/campaigns' ? <button className="btn-secondary text-xs" type="button" onClick={() => postAction(item, 'duplicate')}>Duplicate</button> : null}
                {path === '/campaigns' && item.status !== 'Archived' ? <button className="btn-secondary text-xs" type="button" onClick={() => postAction(item, item.status === 'Inactive' ? 'resume' : 'pause')}>{item.status === 'Inactive' ? 'Resume' : 'Pause'}</button> : null}
                {path === '/employees' && item.status !== 'Archived' ? <button className="btn-secondary text-xs" type="button" onClick={() => postAction(item, item.status === 'Paused' ? 'resume' : 'pause')}>{item.status === 'Paused' ? 'Resume' : 'Pause'}</button> : null}
                {path === '/employees' && item.status !== 'Archived' ? <button className="btn text-xs" type="button" onClick={() => postAction(item, 'run')}>Run</button> : null}
                {path === '/employees' && item.status !== 'Archived' ? <button className="btn-secondary text-xs" type="button" onClick={() => postAction(item, 'dry-run')}>Dry Run</button> : null}
                {path === '/schedules' && item.status !== 'Archived' ? <button className="btn-secondary text-xs" type="button" onClick={() => postAction(item, item.is_paused ? 'resume' : 'pause')}>{item.is_paused ? 'Resume' : 'Pause'}</button> : null}
                {path === '/schedules' && item.status !== 'Archived' ? <button className="btn text-xs" type="button" onClick={() => postAction(item, 'run')}>Run Now</button> : null}
                {path === '/schedules' && item.status !== 'Archived' ? <button className="btn-secondary text-xs" type="button" onClick={() => postAction(item, 'dry-run')}>Dry Run</button> : null}
                {path === '/schedules' && item.status !== 'Archived' ? <button className="btn-secondary text-xs" type="button" onClick={() => postAction(item, 'test-run')}>Test Run</button> : null}
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
