import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { API } from './api';

export async function serverApi<T>(path: string, fallback: T): Promise<T> {
  let response: Response;
  try {
    const cookieHeader = (await cookies()).toString();
    response = await fetch(`${API}/api${path}`, {
      cache: 'no-store',
      headers: cookieHeader ? { cookie: cookieHeader } : {},
    });
  } catch {
    return fallback;
  }
  if (response.status === 401) redirect('/login?expired=1');
  if (!response.ok) return fallback;
  return response.json();
}
