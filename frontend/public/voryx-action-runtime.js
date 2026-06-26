(() => {
  if (window.__voryxActionRuntimeAttached) return;
  window.__voryxActionRuntimeAttached = true;
  const storageKey = 'voryxLastAction';

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

  const fetchSyncStatus = async () => {
    const response = await fetch('/api/sync/status', { credentials: 'include', cache: 'no-store' });
    if (!response.ok) return null;
    return response.json();
  };

  document.addEventListener('click', async (event) => {
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
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
    document.addEventListener('DOMContentLoaded', renderStoredAction);
  } else {
    renderStoredAction();
  }
})();
