'use client';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

function parseValue(sample: unknown, value: string) {
  if (typeof sample === 'number') return Number(value || 0);
  if (typeof sample === 'boolean') return value === 'true';
  if (sample && typeof sample === 'object') {
    try { return JSON.parse(value || '{}'); } catch { return sample; }
  }
  return value;
}

export default function CrudPage({ title, path, defaults }: { title: string; path: string; defaults: Record<string, unknown> }) {
  const [items, setItems] = useState<any[]>([]);
  const [form, setForm] = useState(defaults);
  const [editingId, setEditingId] = useState('');
  const [error, setError] = useState('');

  async function load() {
    try {
      setItems(await api(path));
      setError('');
    } catch {
      location.href = '/login';
    }
  }

  useEffect(() => { load(); }, []);

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
        await api(`${path}/${item.id}`, { method: 'PUT', body: JSON.stringify({ ...item, status: 'Active' }) });
      } else {
        await api(`${path}/${item.id}`, { method: 'DELETE' });
      }
      await load();
    } catch (err: any) {
      setError(err.message || 'Request failed');
    }
  }

  function edit(item: any) {
    const next = { ...defaults };
    for (const key of Object.keys(defaults)) next[key] = item[key] ?? defaults[key];
    setForm(next);
    setEditingId(item.id);
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">{title}</h1>
        <div className="text-sm text-zinc-400">{items.length} records</div>
      </div>
      <div className="card">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Object.keys(defaults).map((key) => {
            const sample = defaults[key];
            const value = form[key];
            return (
              <label key={key} className="grid gap-1 text-sm text-zinc-300">
                <span>{key.replaceAll('_', ' ')}</span>
                {typeof sample === 'boolean' ? (
                  <select className="input" value={String(value)} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })}>
                    <option value="false">false</option>
                    <option value="true">true</option>
                  </select>
                ) : sample && typeof sample === 'object' ? (
                  <textarea className="input min-h-24 font-mono text-xs" value={JSON.stringify(value ?? {}, null, 2)} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })} />
                ) : (
                  <input className="input" value={String(value ?? '')} onChange={(event) => setForm({ ...form, [key]: parseValue(sample, event.target.value) })} />
                )}
              </label>
            );
          })}
        </div>
        {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn" onClick={save}>{editingId ? 'Save Changes' : 'Create'}</button>
          {editingId ? <button className="btn-secondary" onClick={() => { setEditingId(''); setForm(defaults); }}>Cancel</button> : null}
        </div>
      </div>
      <div className="overflow-hidden border border-zinc-800">
        {items.map((item) => (
          <div className="border-b border-zinc-800 bg-zinc-950/60 p-4 last:border-b-0" key={item.id}>
            <div className="mb-3 flex flex-wrap gap-2">
              <button className="btn-secondary text-xs" type="button" onClick={() => edit(item)}>Edit</button>
              <button className="btn-secondary text-xs" type="button" onClick={() => archive(item)}>{item.status === 'Archived' ? 'Restore' : 'Archive'}</button>
            </div>
            <pre className="overflow-auto text-xs text-zinc-300">{JSON.stringify(item, null, 2)}</pre>
          </div>
        ))}
        {!items.length ? <div className="p-5 text-sm text-zinc-400">No records</div> : null}
      </div>
    </div>
  );
}
