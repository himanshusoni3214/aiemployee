'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { API } from '../lib/api';

export function LogoutButton() {
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  if (!mounted || pathname === '/login') return null;

  async function logout() {
    try {
      await fetch(`${API}/api/auth/logout`, { method: 'POST', credentials: 'include' });
    } catch (error) {
      console.error('Logout failed', error);
    } finally {
      localStorage.removeItem('token');
      document.cookie = 'voryx_token=; Max-Age=0; path=/';
      location.href = '/login';
    }
  }

  return <button type="button" className="btn-secondary ml-auto text-xs" onClick={logout}>Logout</button>;
}
