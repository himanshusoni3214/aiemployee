(() => {
  if (window.__voryxActionRuntimeAttached) return;
  window.__voryxActionRuntimeAttached = true;
  const storageKey = 'voryxLastAction';
  const crudStorageKey = 'voryxLastCrudAction';

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
      let text = action.text;
      let isError = Boolean(action.isError);
      if (action.label === 'run' && action.path) {
        const button = document.querySelector(`button[data-voryx-action-path="${escapeAttribute(action.path)}"]`);
        const cells = button?.closest('tr')?.querySelectorAll('td');
        const state = cells?.[3]?.textContent?.trim();
        const reason = cells?.[7]?.textContent?.trim();
        if (state === 'Error') {
          text = `Run succeeded, but Hermes now reports Error${reason && reason !== '-' ? `: ${reason}` : ''}. Last Hermes refresh: ${action.refreshLabel || '-'}`;
          isError = true;
        }
      }
      showBanner(text, isError);
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

  const capitalize = (value) => `${value.charAt(0).toUpperCase()}${value.slice(1)}`;

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
      console.info(`Hermes ${label} succeeded`, { path, result });
      const sync = await fetchSyncStatus();
      const refreshLabel = formatLocalTime(sync?.last_synced_at);
      const successText = `${label.charAt(0).toUpperCase()}${label.slice(1)} succeeded. Last Hermes refresh: ${refreshLabel}`;
      showMessage(wrapper, successText, false);
      sessionStorage.setItem(storageKey, JSON.stringify({ label, path, text: successText, refreshLabel, isError: false }));
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      renderStoredAction();
      renderStoredCrudAction();
    });
  } else {
    renderStoredAction();
    renderStoredCrudAction();
  }
})();
