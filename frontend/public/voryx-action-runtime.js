(() => {
  if (window.__voryxActionRuntimeAttached) return;
  window.__voryxActionRuntimeAttached = true;
  const storageKey = 'voryxLastAction';
  const crudStorageKey = 'voryxLastCrudAction';
  const selectedCompanyStorageKey = 'voryx:selectedCompanyId';
  const companySelectorResetParams = ['campaign_id', 'employee_id'];

  const formatLocalTime = (value) => {
    if (!value) return '-';
    const normalized = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value.replace(' ', 'T')}Z`;
    const date = new Date(normalized);
    if (Number.isNaN(date.getTime())) return value;
    return new Intl.DateTimeFormat(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(date);
  };

  const showMessage = (wrapper, text, isError) => {
    const message = wrapper?.querySelector('[data-voryx-action-message]');
    if (!message) return;
    message.textContent = text;
    message.hidden = !text;
    message.classList.toggle('text-red-300', Boolean(isError));
    message.classList.toggle('text-emerald-300', !isError);
    message.title = text;
  };

  const showBanner = (text, isError) => {
    const main = document.querySelector('main');
    if (!main) return;
    let banner = document.querySelector('[data-voryx-action-banner]');
    if (!banner) {
      banner = document.createElement('div');
      banner.dataset.voryxActionBanner = 'true';
      banner.className = 'mb-4 rounded border px-3 py-2 text-sm';
      main.prepend(banner);
    }
    banner.textContent = text;
    banner.title = text;
    banner.classList.toggle('border-red-500', Boolean(isError));
    banner.classList.toggle('text-red-300', Boolean(isError));
    banner.classList.toggle('border-emerald-700', !isError);
    banner.classList.toggle('text-emerald-300', !isError);
  };

  const showCrudMessage = (crud, text, isError) => {
    const message = crud?.querySelector('[data-voryx-crud-message]');
    if (!message) return;
    message.textContent = text;
    message.hidden = !text;
    message.classList.toggle('text-red-300', Boolean(isError));
    message.classList.toggle('text-emerald-300', !isError);
    message.title = text;
  };

  const escapeAttribute = (value) => {
    if (window.CSS?.escape) return window.CSS.escape(value);
    return value.replace(/["\\]/g, '\\$&');
  };

  const renderStoredAction = () => {
    const raw = sessionStorage.getItem(storageKey);
    if (!raw) return;
    sessionStorage.removeItem(storageKey);
    try {
      const action = JSON.parse(raw);
      showBanner(action.text, Boolean(action.isError));
    } catch (error) {
      console.error('Could not render stored Hermes action status', error);
    }
  };

  const renderStoredCrudAction = () => {
    const raw = sessionStorage.getItem(crudStorageKey);
    if (!raw) return;
    sessionStorage.removeItem(crudStorageKey);
    try {
      const action = JSON.parse(raw);
      showBanner(action.text || 'Dashboard action completed', Boolean(action.isError));
    } catch (error) {
      console.error('Could not render stored dashboard action status', error);
    }
  };

  const localizeStaticTimes = () => {
    document.querySelectorAll('time[datetime]').forEach((node) => {
      const value = node.getAttribute('datetime');
      const label = formatLocalTime(value);
      if (label && label !== '-') {
        node.textContent = label;
        node.title = value || label;
      }
    });
    document.querySelectorAll('[data-voryx-sync-last]').forEach((node) => {
      const value = node.getAttribute('data-voryx-sync-last');
      node.textContent = `Last synced: ${value ? formatLocalTime(value) : '-'}`;
      if (value) node.title = value;
    });
  };

  const capitalize = (value) => `${value.charAt(0).toUpperCase()}${value.slice(1)}`;

  const normalizeCompanySelection = (select) => {
    const value = select?.value || '';
    if (value === '__all') return 'all';
    return value;
  };

  const buildCompanySelectorUrl = (select) => {
    const param = select?.dataset.voryxCompanyParam || 'company_id';
    const selectedCompanyId = normalizeCompanySelection(select);
    const url = new URL(window.location.href);
    companySelectorResetParams.forEach((resetParam) => url.searchParams.delete(resetParam));
    if (selectedCompanyId === 'all') {
      url.searchParams.set(param, 'all');
    } else if (selectedCompanyId) {
      url.searchParams.set(param, selectedCompanyId);
    } else {
      url.searchParams.delete(param);
    }
    return { url, selectedCompanyId };
  };

  const updateSelectedCompanyStorage = (selectedCompanyId) => {
    if (selectedCompanyId && selectedCompanyId !== 'all') {
      localStorage.setItem(selectedCompanyStorageKey, selectedCompanyId);
      return;
    }
    localStorage.removeItem(selectedCompanyStorageKey);
  };

  const urlsEquivalent = (leftHref, rightHref) => {
    const left = new URL(leftHref, window.location.href);
    const right = new URL(rightHref, window.location.href);
    return left.pathname === right.pathname && left.search === right.search && left.hash === right.hash;
  };

  const handleCompanySelectorChange = (event, select) => {
    const { url, selectedCompanyId } = buildCompanySelectorUrl(select);
    const targetHref = url.toString();
    if (urlsEquivalent(window.location.href, targetHref)) {
      updateSelectedCompanyStorage(selectedCompanyId);
      return;
    }
    if (select.dataset.voryxCompanyFallbackHref === targetHref) return;
    select.dataset.voryxCompanyFallbackHref = targetHref;

    window.setTimeout(() => {
      if (select.dataset.voryxCompanyFallbackHref !== targetHref) return;
      delete select.dataset.voryxCompanyFallbackHref;
      if (urlsEquivalent(window.location.href, targetHref)) return;
      if (select.dataset.voryxReactNavigationHref && urlsEquivalent(select.dataset.voryxReactNavigationHref, targetHref) && urlsEquivalent(window.location.href, targetHref)) return;
      updateSelectedCompanyStorage(selectedCompanyId);
      window.location.assign(url.toString());
    }, select.dataset.voryxReactNavigationHref ? 250 : 50);
  };

  const apiPost = async (path, init = {}) => {
    const token = localStorage.getItem('token');
    const response = await fetch(`/api${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers || {}),
      },
      cache: 'no-store',
    });
    const text = await response.text();
    let result = text;
    try { result = text ? JSON.parse(text) : null; } catch {}
    if (response.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login?expired=1';
      return null;
    }
    if (!response.ok) {
      throw new Error(`${init.method || 'GET'} /api${path} failed (${response.status}): ${text}`);
    }
    return result;
  };

  const crudDefaults = (crud) => {
    try { return JSON.parse(crud?.dataset.voryxCrudDefaults || '{}'); } catch { return {}; }
  };

  const parseCrudValue = (sample, type, value) => {
    if (type === 'number' || typeof sample === 'number') return Number(value || 0);
    if (type === 'boolean' || typeof sample === 'boolean') return value === 'true';
    if (type === 'json' || (sample && typeof sample === 'object' && !Array.isArray(sample))) {
      return JSON.parse(value || '{}');
    }
    return value;
  };

  const readCrudForm = (crud) => {
    const defaults = crudDefaults(crud);
    const payload = { ...defaults };
    Object.keys(defaults).forEach((key) => {
      const field = crud.querySelector(`[data-voryx-crud-field="${escapeAttribute(key)}"]`);
      if (!field) return;
      const type = field.dataset.voryxCrudType;
      if (type === 'days') {
        payload[key] = Array.from(field.querySelectorAll('input[type="checkbox"]:checked')).map((input) => input.value);
        return;
      }
      if (type === 'hours') {
        payload[key] = {
          start: field.querySelector('[data-voryx-crud-hour="start"]')?.value || '09:00',
          end: field.querySelector('[data-voryx-crud-hour="end"]')?.value || '17:00',
        };
        return;
      }
      payload[key] = parseCrudValue(defaults[key], type, field.value);
    });
    return payload;
  };

  const setCrudField = (crud, key, value) => {
    const field = crud.querySelector(`[data-voryx-crud-field="${escapeAttribute(key)}"]`);
    if (!field) return;
    const type = field.dataset.voryxCrudType;
    if (type === 'days') {
      const values = Array.isArray(value) ? value : [];
      field.querySelectorAll('input[type="checkbox"]').forEach((input) => { input.checked = values.includes(input.value); });
      return;
    }
    if (type === 'hours') {
      field.querySelector('[data-voryx-crud-hour="start"]')?.setAttribute('value', value?.start || '09:00');
      field.querySelector('[data-voryx-crud-hour="end"]')?.setAttribute('value', value?.end || '17:00');
      const start = field.querySelector('[data-voryx-crud-hour="start"]');
      const end = field.querySelector('[data-voryx-crud-hour="end"]');
      if (start) start.value = value?.start || '09:00';
      if (end) end.value = value?.end || '17:00';
      return;
    }
    if (type === 'json') {
      field.value = JSON.stringify(value ?? {}, null, 2);
      return;
    }
    field.value = value == null ? '' : String(value);
  };

  const resetCrudForm = (crud) => {
    const defaults = crudDefaults(crud);
    Object.keys(defaults).forEach((key) => setCrudField(crud, key, defaults[key]));
    crud.dataset.voryxEditingId = '';
    const save = crud.querySelector('[data-voryx-crud-save]');
    if (save) save.textContent = save.dataset.voryxCreateLabel || save.textContent || 'Create';
    const cancel = crud.querySelector('[data-voryx-crud-cancel]');
    if (cancel) cancel.hidden = true;
  };

  const handleCrudClick = async (event, button) => {
    const crud = button.closest('[data-voryx-crud-page]');
    const path = crud?.dataset.voryxCrudPath;
    if (!crud || !path) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();

    const save = button.closest('[data-voryx-crud-save]');
    const edit = button.closest('[data-voryx-crud-edit]');
    const archive = button.closest('[data-voryx-crud-archive]');
    const cancel = button.closest('[data-voryx-crud-cancel]');

    if (cancel) {
      resetCrudForm(crud);
      showCrudMessage(crud, '', false);
      return;
    }

    if (edit) {
      const row = button.closest('[data-voryx-crud-row]');
      let item = {};
      try { item = JSON.parse(row?.dataset.voryxCrudItem || '{}'); } catch {}
      const defaults = crudDefaults(crud);
      Object.keys(defaults).forEach((key) => setCrudField(crud, key, item[key] ?? defaults[key]));
      crud.dataset.voryxEditingId = item.id || '';
      const saveButton = crud.querySelector('[data-voryx-crud-save]');
      if (saveButton) {
        saveButton.dataset.voryxCreateLabel = saveButton.dataset.voryxCreateLabel || saveButton.textContent || 'Create';
        saveButton.textContent = 'Save Changes';
      }
      const cancelButton = crud.querySelector('[data-voryx-crud-cancel]');
      if (cancelButton) cancelButton.hidden = false;
      showCrudMessage(crud, `Editing ${item.name || item.email || item.id || 'record'}`, false);
      crud.querySelector('[data-voryx-crud-field]')?.focus?.();
      return;
    }

    const label = save ? (crud.dataset.voryxEditingId ? 'save' : 'create') : archive ? 'archive' : 'action';
    const busyButtons = [button];
    busyButtons.forEach((item) => { item.disabled = true; });
    showCrudMessage(crud, `${capitalize(label)} started`, false);

    try {
      if (save) {
        const editingId = crud.dataset.voryxEditingId || '';
        const payload = readCrudForm(crud);
        const result = await apiPost(editingId ? `${path}/${editingId}` : path, {
          method: editingId ? 'PUT' : 'POST',
          body: JSON.stringify(payload),
        });
        console.info(`Dashboard ${label} succeeded`, { path, editingId, result });
        const text = `${capitalize(label)} succeeded`;
        showCrudMessage(crud, text, false);
        sessionStorage.setItem(crudStorageKey, JSON.stringify({ text, isError: false }));
        window.setTimeout(() => window.location.reload(), 500);
        return;
      }

      if (archive) {
        const row = button.closest('[data-voryx-crud-row]');
        let item = {};
        try { item = JSON.parse(row?.dataset.voryxCrudItem || '{}'); } catch {}
        if (!item.id) throw new Error('Missing record id');
        let result;
        if (item.status === 'Archived') {
          const defaults = crudDefaults(crud);
          const payload = { ...defaults };
          Object.keys(defaults).forEach((key) => { payload[key] = item[key] ?? defaults[key]; });
          payload.status = path === '/employees' ? 'Stopped' : 'Active';
          result = await apiPost(`${path}/${item.id}`, { method: 'PUT', body: JSON.stringify(payload) });
        } else {
          result = await apiPost(`${path}/${item.id}`, { method: 'DELETE' });
        }
        console.info('Dashboard archive succeeded', { path, id: item.id, result });
        const text = `${item.status === 'Archived' ? 'Restore' : 'Archive'} succeeded`;
        showCrudMessage(crud, text, false);
        sessionStorage.setItem(crudStorageKey, JSON.stringify({ text, isError: false }));
        window.setTimeout(() => window.location.reload(), 500);
      }
    } catch (error) {
      const text = `${capitalize(label)} failed: ${error?.message || 'Request failed'}`;
      console.error(`Dashboard ${label} failed`, { path, error });
      showCrudMessage(crud, text, true);
      showBanner(text, true);
    } finally {
      busyButtons.forEach((item) => { item.disabled = false; });
    }
  };

  const fetchSyncStatus = async () => {
    const response = await fetch('/api/sync/status', { credentials: 'include', cache: 'no-store' });
    if (!response.ok) return null;
    return response.json();
  };

  const fetchJob = async (jobId) => {
    const token = localStorage.getItem('token');
    const response = await fetch(`/api/jobs/${jobId}`, {
      credentials: 'include',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      cache: 'no-store',
    });
    const text = await response.text();
    let result = text;
    try { result = text ? JSON.parse(text) : null; } catch {}
    if (response.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login?expired=1';
      return null;
    }
    if (!response.ok) {
      console.error('Job status request failed', { jobId, status: response.status, body: text });
      return null;
    }
    return result;
  };

  const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const terminalStates = new Set(['completed', 'failed', 'blocked', 'cancelled', 'skipped']);
  const problemStates = new Set(['failed', 'blocked', 'cancelled', 'skipped']);

  const actionResultState = (result) => String(result?.state || result?.status || '').toLowerCase();
  const isProblemAction = (result) => problemStates.has(actionResultState(result)) || result?.ok === false;
  const actionMessage = (label, result, refreshLabel) => {
    const base = result?.message || `${capitalize(label)} request accepted`;
    return `${base}. Last Hermes refresh: ${refreshLabel || '-'}`;
  };

  const jobMessage = (label, job, refreshLabel) => {
    const state = String(job?.status || 'unknown').toLowerCase();
    const detail = job?.error_message || job?.logs?.[job.logs.length - 1] || '';
    const suffix = detail ? `: ${detail}` : '';
    return `${capitalize(label)} ${state}${suffix}. Job ID: ${job?.id || '-'}. Last Hermes refresh: ${refreshLabel || '-'}`;
  };

  const pollJobStatus = async ({ jobId, label, wrapper, refreshLabel }) => {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      await sleep(1500);
      const job = await fetchJob(jobId);
      if (!job) continue;
      const state = String(job.status || '').toLowerCase();
      if (!terminalStates.has(state)) {
        showMessage(wrapper, jobMessage(label, job, refreshLabel), false);
        continue;
      }
      const isError = problemStates.has(state);
      const text = jobMessage(label, job, refreshLabel);
      showMessage(wrapper, text, isError);
      return { text, isError, state };
    }
    return null;
  };

  document.addEventListener('click', async (event) => {
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
    const crudButton = target?.closest?.('[data-voryx-crud-save], [data-voryx-crud-edit], [data-voryx-crud-archive], [data-voryx-crud-cancel]');
    if (crudButton) {
      await handleCrudClick(event, crudButton);
      return;
    }

    const button = target?.closest?.('button[data-voryx-action-path]');
    if (!button) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();
    if (button.disabled || button.dataset.voryxBusy === 'true') return;

    const path = button.dataset.voryxActionPath;
    const label = button.dataset.voryxActionLabel || 'action';
    const wrapper = button.closest('[data-voryx-action-wrapper]');
    const wrapperButtons = Array.from(wrapper?.querySelectorAll('button[data-voryx-action-path]') || [button]);
    const token = localStorage.getItem('token');
    button.dataset.voryxBusy = 'true';
    wrapperButtons.forEach((item) => { item.disabled = true; });
    showMessage(wrapper, `${label.charAt(0).toUpperCase()}${label.slice(1)} started`, false);

    try {
      const response = await fetch(`/api${path}`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        cache: 'no-store',
      });
      const text = await response.text();
      let result = text;
      try { result = text ? JSON.parse(text) : null; } catch {}
      if (response.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/login?expired=1';
        return;
      }
      if (!response.ok) {
        console.error('API request failed', { path, status: response.status, body: text });
        throw new Error(`POST /api${path} failed (${response.status}): ${text}`);
      }
      console.info(`Hermes ${label} completed`, { path, result });
      const sync = await fetchSyncStatus();
      const refreshLabel = formatLocalTime(sync?.last_synced_at);
      let textToStore = actionMessage(label, result, refreshLabel);
      let isError = isProblemAction(result);
      showMessage(wrapper, textToStore, isError);
      if (result?.job_id && !result?.terminal) {
        const polled = await pollJobStatus({ jobId: result.job_id, label, wrapper, refreshLabel });
        if (polled) {
          textToStore = polled.text;
          isError = polled.isError;
        }
      }
      sessionStorage.setItem(storageKey, JSON.stringify({ label, path, text: textToStore, refreshLabel, isError, state: actionResultState(result), jobId: result?.job_id }));
      window.setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      const failedText = `${label.charAt(0).toUpperCase()}${label.slice(1)} failed: ${error?.message || 'Action failed'}`;
      console.error(`Hermes ${label} failed`, { path, error });
      showMessage(wrapper, failedText, true);
      showBanner(failedText, true);
    } finally {
      delete button.dataset.voryxBusy;
      wrapperButtons.forEach((item) => { item.disabled = false; });
    }
  }, true);

  document.addEventListener('change', (event) => {
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
    const select = target?.closest?.('select[data-voryx-company-selector]');
    if (!select) return;
    handleCompanySelectorChange(event, select);
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      localizeStaticTimes();
      renderStoredAction();
      renderStoredCrudAction();
    });
  } else {
    localizeStaticTimes();
    renderStoredAction();
    renderStoredCrudAction();
  }

  const observer = new MutationObserver(() => {
    window.clearTimeout(window.__voryxLocalizeTimer);
    window.__voryxLocalizeTimer = window.setTimeout(localizeStaticTimes, 50);
  });
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
  }
})();
