'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '../lib/api';

function usePostAction() {
  const router = useRouter();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function post(path: string, label: string) {
    setBusy(label);
    setError('');
    try {
      await api(path, { method: 'POST' });
      router.refresh();
    } catch (err: any) {
      setError(err?.message || 'Action failed');
    } finally {
      setBusy('');
    }
  }

  return { busy, error, post };
}

export function EmployeeActions({ id, status }: { id: string; status: string }) {
  const { busy, error, post } = usePostAction();
  const isRunning = status === 'Running';
  const toggleAction = isRunning ? 'pause' : 'resume';

  return (
    <div className="min-w-44 space-y-2">
      <div className="flex flex-wrap gap-2">
        <button className="btn-secondary text-xs" disabled={Boolean(busy)} onClick={() => post(`/employees/${id}/${toggleAction}`, toggleAction)}>
          {isRunning ? 'Pause' : 'Resume'}
        </button>
        <button className="btn text-xs" disabled={Boolean(busy)} onClick={() => post(`/employees/${id}/run`, 'run')}>
          Run
        </button>
      </div>
      {error ? <div className="max-w-44 truncate text-xs text-red-300" title={error}>{error}</div> : null}
    </div>
  );
}

export function ScheduleActions({ id, isPaused }: { id: string; isPaused: boolean }) {
  const { busy, error, post } = usePostAction();
  const toggleAction = isPaused ? 'resume' : 'pause';

  return (
    <div className="min-w-44 space-y-2">
      <div className="flex flex-wrap gap-2">
        <button className="btn-secondary text-xs" disabled={Boolean(busy)} onClick={() => post(`/schedules/${id}/${toggleAction}`, toggleAction)}>
          {isPaused ? 'Resume' : 'Pause'}
        </button>
        <button className="btn text-xs" disabled={Boolean(busy)} onClick={() => post(`/schedules/${id}/run`, 'run')}>
          Run
        </button>
      </div>
      {error ? <div className="max-w-44 truncate text-xs text-red-300" title={error}>{error}</div> : null}
    </div>
  );
}
