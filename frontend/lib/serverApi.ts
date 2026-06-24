import { cookies } from 'next/headers';
import { API } from './api';

export async function serverApi<T>(path: string, fallback: T): Promise<T> {
  try {
    const cookieHeader = (await cookies()).toString();
    const response = await fetch(`${API}/api${path}`, {
      cache: 'no-store',
      headers: cookieHeader ? { cookie: cookieHeader } : {},
    });
    if (!response.ok) return fallback;
    return response.json();
  } catch {
    return fallback;
  }
}
