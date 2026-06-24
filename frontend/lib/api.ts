export const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
export async function api(path: string, init: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  const res = await fetch(`${API}/api${path}`, { ...init, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers || {}) }, cache: 'no-store' });
  if (!res.ok) throw new Error(await res.text());
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
