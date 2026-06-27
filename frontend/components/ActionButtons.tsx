'use client';

import { useState } from 'react';
import type { MouseEvent } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '../lib/api';

function usePostAction() {
  const router = useRouter();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  async function post(path: string, label: string) {
    setBusy(label);
    setError('');
    setMessage('');
    try {
      const result = await api(path, { method: 'POST' });
      const resultState = String(result?.state || result?.status || '').toLowerCase();
      const text = result?.message || `${label.charAt(0).toUpperCase()}${label.slice(1)} request accepted`;
      console.info(`Hermes ${label} completed`, { path, result });
      if (['failed', 'blocked', 'cancelled', 'skipped'].includes(resultState) || result?.ok === false) {
        setError(text);
      } else {
        setMessage(text);
      }
      router.refresh();
    } catch (err: any) {
      const detail = err?.message || 'Action failed';
      console.error(`Hermes ${label} failed`, { path, error: err });
      setError(detail);
    } finally {
      setBusy('');
    }
  }

  return { busy, error, message, post };
}

export function EmployeeActions({ id, status }: { id: string; status: string }) {
  const { busy, error, message, post } = usePostAction();
  const isRunning = status === 'Running';
  const toggleAction = isRunning ? 'pause' : 'resume';

  function handleAction(event: MouseEvent<HTMLButtonElement>, path: string, label: string) {
    event.preventDefault();
    event.stopPropagation();
    void post(path, label);
  }

  return (
    <div className="min-w-44 space-y-2" data-voryx-action-wrapper>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-secondary text-xs" disabled={Boolean(busy)} data-voryx-action-label={toggleAction} data-voryx-action-path={`/employees/${id}/${toggleAction}`} onClick={(event) => handleAction(event, `/employees/${id}/${toggleAction}`, toggleAction)}>
          {isRunning ? 'Pause' : 'Resume'}
        </button>
        <button type="button" className="btn text-xs" disabled={Boolean(busy)} data-voryx-action-label="run" data-voryx-action-path={`/employees/${id}/run`} onClick={(event) => handleAction(event, `/employees/${id}/run`, 'run')}>
          Run
        </button>
      </div>
      <div hidden className="max-w-44 truncate text-xs text-emerald-300" data-voryx-action-message />
      {message ? <div className="max-w-44 truncate text-xs text-emerald-300" title={message}>{message}</div> : null}
      {error ? <div className="max-w-44 truncate text-xs text-red-300" title={error}>{error}</div> : null}
    </div>
  );
}

export function ScheduleActions({ id, isPaused }: { id: string; isPaused: boolean }) {
  const { busy, error, message, post } = usePostAction();
  const toggleAction = isPaused ? 'resume' : 'pause';

  function handleAction(event: MouseEvent<HTMLButtonElement>, path: string, label: string) {
    event.preventDefault();
    event.stopPropagation();
    void post(path, label);
  }

  return (
    <div className="min-w-44 space-y-2" data-voryx-action-wrapper>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-secondary text-xs" disabled={Boolean(busy)} data-voryx-action-label={toggleAction} data-voryx-action-path={`/schedules/${id}/${toggleAction}`} onClick={(event) => handleAction(event, `/schedules/${id}/${toggleAction}`, toggleAction)}>
          {isPaused ? 'Resume' : 'Pause'}
        </button>
        <button type="button" className="btn text-xs" disabled={Boolean(busy)} data-voryx-action-label="run" data-voryx-action-path={`/schedules/${id}/run`} onClick={(event) => handleAction(event, `/schedules/${id}/run`, 'run')}>
          Run
        </button>
      </div>
      <div hidden className="max-w-44 truncate text-xs text-emerald-300" data-voryx-action-message />
      {message ? <div className="max-w-44 truncate text-xs text-emerald-300" title={message}>{message}</div> : null}
      {error ? <div className="max-w-44 truncate text-xs text-red-300" title={error}>{error}</div> : null}
    </div>
  );
}
