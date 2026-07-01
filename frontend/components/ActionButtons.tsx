'use client';

import { useState } from 'react';
import type { MouseEvent } from 'react';
import { useRouter } from 'next/navigation';
import { api } from '../lib/api';
import { isSafetyLockedHermesJob } from '../lib/hermesSafety';

export type ConnectorCapabilities = {
  connector_mode?: string;
  supports_pause_resume?: boolean;
  supports_manual_run?: boolean;
  supports_dry_run?: boolean;
  manual_run_message?: string | null;
};

export const defaultConnectorCapabilities: ConnectorCapabilities = {
  connector_mode: 'jobs_json',
  supports_pause_resume: true,
  supports_manual_run: false,
  supports_dry_run: false,
  manual_run_message: 'Manual run unavailable in jobs_json mode',
};

function capabilitiesOrDefault(capabilities?: ConnectorCapabilities | null): ConnectorCapabilities {
  return { ...defaultConnectorCapabilities, ...(capabilities || {}) };
}

export function ManualRunUnavailable({ capabilities }: { capabilities?: ConnectorCapabilities | null }) {
  const caps = capabilitiesOrDefault(capabilities);
  if (caps.supports_manual_run || caps.supports_dry_run) return null;
  return (
    <div className="max-w-48 text-xs text-zinc-400" data-voryx-manual-run-unavailable>
      {caps.manual_run_message || `Manual run unavailable in ${caps.connector_mode || 'current'} mode`}
    </div>
  );
}

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

export function EmployeeActions({ id, status, hermesJobId, capabilities, showUnavailableMessage = true }: { id: string; status: string; hermesJobId?: string | null; capabilities?: ConnectorCapabilities | null; showUnavailableMessage?: boolean }) {
  const { busy, error, message, post } = usePostAction();
  const caps = capabilitiesOrDefault(capabilities);
  const safetyLocked = isSafetyLockedHermesJob(hermesJobId);
  const canPause = Boolean(caps.supports_pause_resume) && (status === 'Running' || status === 'Scheduled');
  const canResume = Boolean(caps.supports_pause_resume) && (status === 'Paused' || status === 'Stopped');
  const canRun = Boolean(caps.supports_manual_run) && status === 'Scheduled';
  const toggleAction = canPause ? 'pause' : 'resume';

  function handleAction(event: MouseEvent<HTMLButtonElement>, path: string, label: string) {
    event.preventDefault();
    event.stopPropagation();
    void post(path, label);
  }

  return (
    <div className="min-w-44 space-y-2" data-voryx-action-wrapper data-voryx-connector-mode={caps.connector_mode || 'unknown'}>
      {safetyLocked ? (
        <div className="rounded border border-amber-700 px-2 py-1 text-xs text-amber-300" title="Safety blocked: this worker can send real Gmail prospect outreach.">Locked</div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {(canPause || canResume) ? (
            <button type="button" className="btn-secondary text-xs" disabled={Boolean(busy)} data-voryx-action-label={toggleAction} data-voryx-action-path={`/employees/${id}/${toggleAction}`} onClick={(event) => handleAction(event, `/employees/${id}/${toggleAction}`, toggleAction)}>
              {canPause ? 'Pause' : 'Resume'}
            </button>
          ) : null}
          {canRun ? (
            <button type="button" className="btn text-xs" disabled={Boolean(busy)} data-voryx-action-label="run" data-voryx-action-path={`/employees/${id}/run`} onClick={(event) => handleAction(event, `/employees/${id}/run`, 'run')}>
              Run
            </button>
          ) : null}
        </div>
      )}
      {showUnavailableMessage && !safetyLocked && status === 'Scheduled' ? <ManualRunUnavailable capabilities={caps} /> : null}
      <div hidden className="max-w-44 truncate text-xs text-emerald-300" data-voryx-action-message />
      {message ? <div className="max-w-44 truncate text-xs text-emerald-300" title={message}>{message}</div> : null}
      {error ? <div className="max-w-44 truncate text-xs text-red-300" title={error}>{error}</div> : null}
    </div>
  );
}

export function ScheduleActions({ id, isPaused, hermesJobId, capabilities, showUnavailableMessage = true }: { id: string; isPaused: boolean; hermesJobId?: string | null; capabilities?: ConnectorCapabilities | null; showUnavailableMessage?: boolean }) {
  const { busy, error, message, post } = usePostAction();
  const caps = capabilitiesOrDefault(capabilities);
  const safetyLocked = isSafetyLockedHermesJob(hermesJobId);
  const canToggle = Boolean(caps.supports_pause_resume);
  const canRun = Boolean(caps.supports_manual_run) && !isPaused;
  const toggleAction = isPaused ? 'resume' : 'pause';

  function handleAction(event: MouseEvent<HTMLButtonElement>, path: string, label: string) {
    event.preventDefault();
    event.stopPropagation();
    void post(path, label);
  }

  return (
    <div className="min-w-44 space-y-2" data-voryx-action-wrapper data-voryx-connector-mode={caps.connector_mode || 'unknown'}>
      {safetyLocked ? (
        <div className="rounded border border-amber-700 px-2 py-1 text-xs text-amber-300" title="Safety blocked: this schedule can send real Gmail prospect outreach.">Locked</div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {canToggle ? (
            <button type="button" className="btn-secondary text-xs" disabled={Boolean(busy)} data-voryx-action-label={toggleAction} data-voryx-action-path={`/schedules/${id}/${toggleAction}`} onClick={(event) => handleAction(event, `/schedules/${id}/${toggleAction}`, toggleAction)}>
              {isPaused ? 'Resume' : 'Pause'}
            </button>
          ) : null}
          {canRun ? (
            <button type="button" className="btn text-xs" disabled={Boolean(busy)} data-voryx-action-label="run" data-voryx-action-path={`/schedules/${id}/run`} onClick={(event) => handleAction(event, `/schedules/${id}/run`, 'run')}>
              Run
            </button>
          ) : null}
        </div>
      )}
      {showUnavailableMessage && !safetyLocked && !isPaused ? <ManualRunUnavailable capabilities={caps} /> : null}
      <div hidden className="max-w-44 truncate text-xs text-emerald-300" data-voryx-action-message />
      {message ? <div className="max-w-44 truncate text-xs text-emerald-300" title={message}>{message}</div> : null}
      {error ? <div className="max-w-44 truncate text-xs text-red-300" title={error}>{error}</div> : null}
    </div>
  );
}
