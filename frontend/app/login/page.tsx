'use client';
import { useState } from 'react';
import { API } from '../../lib/api';

export default function Login() {
  const [email, setEmail] = useState('admin@themealz.com');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');

  async function submit() {
    setError('');
    const response = await fetch(`${API}/api/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
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
        <div className="grid gap-3">
          <label className="grid gap-1 text-sm text-zinc-300"><span>Email</span><input className="input" value={email} onChange={(event) => setEmail(event.target.value)} /></label>
          <label className="grid gap-1 text-sm text-zinc-300"><span>Password</span><input className="input" type="password" value={password} onChange={(event) => setPassword(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') submit(); }} /></label>
        </div>
        {error ? <p className="mt-3 text-sm text-red-300">{error}</p> : null}
        <button className="btn mt-5 w-full" onClick={submit}>Login</button>
      </div>
    </div>
  );
}
