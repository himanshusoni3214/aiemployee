'use client';
import type { FormEvent } from 'react';
import { useEffect, useState } from 'react';
import { API } from '../../lib/api';

export default function Login() {
  const [email, setEmail] = useState('admin@themealz.com');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('expired')) {
      setNotice('Session expired, please login again.');
    }
  }, []);

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
    location.href = '/dashboard';
  }

  return (
    <div className="mx-auto mt-16 max-w-md">
      <div className="card">
        <h1 className="mb-5 text-2xl font-semibold">Login</h1>
        <form action={`${API}/api/auth/login-form`} method="post" onSubmit={submit}>
          <div className="grid gap-3">
            <label className="grid gap-1 text-sm text-zinc-300"><span>Email</span><input className="input" name="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
            <label className="grid gap-1 text-sm text-zinc-300"><span>Password</span><input className="input" name="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
          </div>
          {notice ? <p className="mt-3 text-sm text-amber-300">{notice}</p> : null}
          {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
          <button className="btn mt-5 w-full" type="submit">Login</button>
        </form>
      </div>
    </div>
  );
}
