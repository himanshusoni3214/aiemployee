'use client';

import { useState } from 'react';
import { api } from '../lib/api';

type LeadSchema = {
  locked_fields?: string[];
  custom_fields?: Array<{ name: string; label?: string; hidden?: boolean; order?: number }>;
  columns?: string[];
};

export function LeadSchemaEditor({ campaignId, initialSchema }: { campaignId: string; initialSchema: LeadSchema }) {
  const [text, setText] = useState(JSON.stringify(initialSchema?.custom_fields || [], null, 2));
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      const customFields = JSON.parse(text || '[]');
      const result = await api(`/campaigns/${campaignId}/lead-schema`, {
        method: 'PUT',
        body: JSON.stringify({ custom_fields: customFields }),
      });
      setText(JSON.stringify(result?.lead_schema?.custom_fields || customFields, null, 2));
      setMessage(result?.message || 'Lead schema saved');
    } catch (err: any) {
      setError(err?.message || 'Lead schema save failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 rounded border border-zinc-800 p-3" data-voryx-lead-schema-editor>
      <div className="mb-2 text-xs text-zinc-400">Lead sheet fields. Locked system fields are always included; edit custom fields only.</div>
      <textarea
        className="input min-h-28 font-mono text-xs"
        value={text}
        onChange={(event) => setText(event.target.value)}
        data-voryx-lead-schema-custom-fields
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" className="btn-secondary text-xs" disabled={busy} onClick={save} data-voryx-lead-schema-save>
          Save lead fields
        </button>
        <span className="text-xs text-zinc-500">{(initialSchema?.locked_fields || []).length} locked fields</span>
      </div>
      {message ? <div className="mt-2 text-xs text-emerald-300">{message}</div> : null}
      {error ? <div className="mt-2 text-xs text-red-300">{error}</div> : null}
    </div>
  );
}
