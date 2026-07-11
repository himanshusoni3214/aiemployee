export const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
function errorMessage(method: string, path: string, status: number, body: string) {
  let detail: any = body;
  try {
    detail = JSON.parse(body);
  } catch {
    detail = body;
  }
  const message = typeof detail?.detail?.message === 'string'
    ? detail.detail.message
    : typeof detail?.message === 'string'
      ? detail.message
      : typeof detail?.detail === 'string'
        ? detail.detail
        : typeof detail === 'string'
          ? detail
          : 'Request failed';
  return `${method} /api${path} failed (${status}): ${message}`;
}
export async function api(path: string, init: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  const res = await fetch(`${API}/api${path}`, { ...init, credentials: 'include', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers || {}) }, cache: 'no-store' });
  if (res.status === 401 && typeof window !== 'undefined') {
    localStorage.removeItem('token');
    location.href = '/login?expired=1';
  }
  if (!res.ok) {
    const body = await res.text();
    console.error('API request failed', { path, status: res.status, body });
    throw new Error(errorMessage(init.method || 'GET', path, res.status, body));
  }
  return res.json();
}
export function authHeaders() {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  return { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) };
}
export function logout() {
  localStorage.removeItem('token');
  document.cookie = 'voryx_token=; Max-Age=0; path=/';
  location.href = '/login';
}
