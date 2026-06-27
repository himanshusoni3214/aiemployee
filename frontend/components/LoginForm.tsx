'use client';

import type { FormEvent } from 'react';
import { useState } from 'react';
import { API } from '../lib/api';

function safeRedirect(path: string) {
  return path.startsWith('/') && !path.startsWith('//') ? path : '/dashboard';
}

export function LoginForm({ notice: initialNotice = '', redirectTo = '/dashboard' }: { notice?: string; redirectTo?: string }) {
  const [email, setEmail] = useState('admin@themealz.com');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState(initialNotice);

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    setError('');
    setNotice('');
    const response = await fetch(`${API}/api/auth/login`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
    if (!response.ok) {
      setError('Invalid email or password');
      return;
    }
    const data = await response.json();
    localStorage.setItem('token', data.access_token);
    location.href = safeRedirect(redirectTo);
  }

  return (
    <div className="mx-auto mt-16 max-w-md">
      <div className="card">
        <h1 className="mb-5 text-2xl font-semibold">Login</h1>
        <form action={`${API}/api/auth/login-form`} method="post" onSubmit={submit}>
          <input type="hidden" name="redirect_to" value={safeRedirect(redirectTo)} />
          <div className="grid gap-3">
            <div className="grid gap-1 text-sm text-zinc-300"><label htmlFor="login-email">Email</label><input id="login-email" className="input" name="email" value={email} onChange={(event) => setEmail(event.target.value)} /></div>
            <div className="grid gap-1 text-sm text-zinc-300"><label htmlFor="login-password">Password</label><input id="login-password" className="input" name="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></div>
          </div>
          {notice ? <p className="mt-3 text-sm text-amber-300">{notice}</p> : null}
          {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
          <button className="btn mt-5 w-full" type="submit">Login</button>
        </form>
      </div>
    </div>
  );
}
