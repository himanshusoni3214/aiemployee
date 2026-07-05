'use client';

import { useState } from 'react';

type LeadOutput = {
  path: string;
  download_url: string;
  row_count: number;
  generated_at: string;
  columns?: string[];
};

export function LeadOutputsPanel({ outputs, rows }: { outputs: LeadOutput[]; rows: Record<string, unknown>[] }) {
  const latest = outputs[0];
  const [copied, setCopied] = useState(false);
  if (!latest) return <div className="text-xs text-zinc-500">No lead CSV generated yet.</div>;

  async function copyPath() {
    await navigator.clipboard.writeText(latest.path);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="mt-3 rounded border border-zinc-800 p-3" data-voryx-lead-outputs>
      <div className="grid gap-1 text-xs text-zinc-400">
        <div>Latest CSV: <span className="text-zinc-200">{latest.path}</span></div>
        <div>Rows: {latest.row_count} / Generated: {latest.generated_at}</div>
      </div>
      <div className="mt-2 flex flex-wrap gap-2">
        <a className="btn-secondary text-xs" href={latest.download_url} data-voryx-download-csv>Download CSV</a>
        <button className="btn-secondary text-xs" type="button" onClick={copyPath} data-voryx-copy-path>{copied ? 'Copied' : 'Copy file path'}</button>
      </div>
      {rows.length ? (
        <div className="mt-3 max-h-64 overflow-auto">
          <table className="ops-table text-xs" data-voryx-view-leads-table>
            <thead><tr>{Object.keys(rows[0]).slice(0, 8).map((column) => <th key={column}>{column}</th>)}</tr></thead>
            <tbody>
              {rows.slice(0, 10).map((row, index) => (
                <tr key={index}>{Object.keys(rows[0]).slice(0, 8).map((column) => <td key={column}>{String(row[column] ?? '')}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-2 text-xs text-zinc-500">
        <span>Filters supported: all leads</span>
        <span>verified only</span>
        <span>missing email</span>
        <span>duplicate suspects</span>
        <span>no website</span>
      </div>
    </div>
  );
}
