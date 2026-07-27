export const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export type ApiBlocker = { code?: string; field?: string | null; message: string };
export type ApiErrorPayload = {
  code?: string;
  message: string;
  stage?: string | null;
  field_errors?: Record<string, string[]>;
  blockers?: ApiBlocker[];
  retryable?: boolean;
  recommended_action?: string | null;
  request_id?: string | null;
};

export class ApiError extends Error {
  status: number;
  code?: string;
  stage?: string | null;
  fieldErrors: Record<string, string[]>;
  blockers: ApiBlocker[];
  retryable: boolean;
  recommendedAction?: string | null;
  requestId?: string | null;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message);
    this.name = 'ApiError';
    this.status = status;
    this.code = payload.code;
    this.stage = payload.stage;
    this.fieldErrors = payload.field_errors || {};
    this.blockers = payload.blockers || [];
    this.retryable = Boolean(payload.retryable);
    this.recommendedAction = payload.recommended_action;
    this.requestId = payload.request_id;
  }
}

function parseBody(body: string, contentType: string) {
  if (!body.trim()) return {};
  if (contentType.includes('application/json')) {
    try {
      return JSON.parse(body);
    } catch {
      return { message: 'The server returned malformed JSON.' };
    }
  }
  return { message: body.slice(0, 500) };
}

function errorPayload(method: string, path: string, status: number, body: string, contentType: string): ApiErrorPayload {
  const parsed: any = parseBody(body, contentType);
  const detail = parsed?.detail?.error || parsed?.error || parsed?.detail || parsed;
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return detail;
  }
  const message = typeof detail === 'string' ? detail : 'Request failed';
  return { message: `${method} /api${path} failed (${status}): ${message}` };
}
export async function api(path: string, init: RequestInit = {}) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  const contentHeaders = init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' };
  const res = await fetch(`${API}/api${path}`, { ...init, credentials: 'include', headers: { ...contentHeaders, ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(init.headers || {}) }, cache: 'no-store' });
  if (res.status === 401 && typeof window !== 'undefined') {
    localStorage.removeItem('token');
    location.href = '/login?expired=1';
  }
  if (!res.ok) {
    const body = await res.text();
    console.error('API request failed', { path, status: res.status, body });
    throw new ApiError(
      res.status,
      errorPayload(init.method || 'GET', path, res.status, body, res.headers.get('content-type') || ''),
    );
  }
  if (res.status === 204) return {};
  const body = await res.text();
  if (!body.trim()) return {};
  return parseBody(body, res.headers.get('content-type') || '');
}
export async function downloadApi(path: string, filename: string) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  const res = await fetch(`${API}/api${path}`, {
    credentials: 'include',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: 'no-store',
  });
  if (!res.ok) {
    const body = await res.text();
    throw new ApiError(
      res.status,
      errorPayload('GET', path, res.status, body, res.headers.get('content-type') || ''),
    );
  }
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
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
