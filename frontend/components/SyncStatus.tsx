'use client';

import { useEffect, useState } from 'react';
import { formatLocalTime } from './LocalTime';

export type SyncInfo = {
  status?: string;
  last_synced_at?: string | null;
  age_seconds?: number | null;
  error?: string | null;
};

function normalizeUtc(value: string) {
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value)) return value;
  return `${value.replace(' ', 'T')}Z`;
}

function statusFor(sync: SyncInfo, now: number) {
  if (sync.error || sync.status === 'failed') return 'Failed';
  if (!sync.last_synced_at) return 'Stale';
  const syncedAt = new Date(normalizeUtc(sync.last_synced_at)).getTime();
  if (Number.isNaN(syncedAt)) return 'Stale';
  const age = Math.floor((now - syncedAt) / 1000);
  return age > 90 ? 'Stale' : 'Live';
}

function color(status: string) {
  if (status === 'Live') return 'text-emerald-300';
  if (status === 'Stale') return 'text-amber-300';
  return 'text-red-300';
}

export function SyncStatus({ sync }: { sync?: SyncInfo }) {
  const [now, setNow] = useState(Date.now());
  const status = statusFor(sync || {}, now);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="text-right text-sm">
      <div className={color(status)}>{status}</div>
      <div className="text-zinc-400">Last synced: {sync?.last_synced_at ? formatLocalTime(sync.last_synced_at) : '-'}</div>
      {sync?.error ? <div className="max-w-lg truncate text-red-300" title={sync.error}>{sync.error}</div> : null}
    </div>
  );
}
