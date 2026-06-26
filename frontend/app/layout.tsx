import './globals.css';
import Link from 'next/link';

const links = [
  ['Dashboard', '/dashboard'],
  ['Companies', '/companies'],
  ['Campaigns', '/campaigns'],
  ['Employees', '/employees'],
  ['Schedules', '/scheduler'],
  ['Jobs', '/jobs'],
  ['Logs', '/reports'],
  ['Health', '/system'],
];

const actionRuntime = `
(() => {
  if (window.__voryxActionRuntimeAttached) return;
  window.__voryxActionRuntimeAttached = true;
  const apiBase = ${JSON.stringify(process.env.NEXT_PUBLIC_API_URL || '')};
  const showMessage = (wrapper, text, isError) => {
    const message = wrapper?.querySelector('[data-voryx-action-message]');
    if (!message) return;
    message.textContent = text;
    message.hidden = !text;
    message.classList.toggle('text-red-300', Boolean(isError));
    message.classList.toggle('text-emerald-300', !isError);
    message.title = text;
  };
  document.addEventListener('click', async (event) => {
    const button = event.target?.closest?.('button[data-voryx-action-path]');
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
    showMessage(wrapper, '', false);

    try {
      const response = await fetch(\`\${apiBase}/api\${path}\`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: \`Bearer \${token}\` } : {}),
        },
        cache: 'no-store',
      });
      const text = await response.text();
      let result = text;
      try { result = text ? JSON.parse(text) : null; } catch {}
      if (!response.ok) {
        console.error('API request failed', { path, status: response.status, body: text });
        throw new Error(\`POST /api\${path} failed (\${response.status}): \${text}\`);
      }
      console.info(\`Hermes \${label} succeeded\`, { path, result });
      showMessage(wrapper, \`\${label.charAt(0).toUpperCase()}\${label.slice(1)} applied\`, false);
      window.setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      console.error(\`Hermes \${label} failed\`, { path, error });
      showMessage(wrapper, error?.message || 'Action failed', true);
    } finally {
      delete button.dataset.voryxBusy;
      wrapperButtons.forEach((item) => { item.disabled = false; });
    }
  }, true);
})();
`;

export default function RootLayout({children}:{children:React.ReactNode}){return <html><body><script dangerouslySetInnerHTML={{__html:actionRuntime}} /><div className="min-h-screen"><nav className="border-b border-zinc-800 bg-zinc-950/90 px-5 py-3"><div className="flex flex-wrap items-center gap-4"><b className="mr-2 text-white">Voryx Ops</b>{links.map(([label, href])=><Link className="text-sm text-zinc-300 hover:text-white" href={href} key={href}>{label}</Link>)}</div></nav><main className="p-5 lg:p-6">{children}</main></div></body></html>}
