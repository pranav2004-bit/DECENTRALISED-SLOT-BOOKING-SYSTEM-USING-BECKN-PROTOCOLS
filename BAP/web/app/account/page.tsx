'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { FormField } from '@/components/ui/FormField';
import { ApiError } from '@/lib/api-client';
import { login, signup } from '@/lib/auth-api';

type Mode = 'login' | 'signup';

export default function AccountPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>('login');
  const [name, setName] = useState('');
  const [contact, setContact] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === 'signup') {
        await signup(name.trim(), contact.trim(), password);
      }
      await login(contact.trim(), password);
      router.push('/search');
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Something went wrong');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
        {mode === 'login' ? 'Log in' : 'Create an account'}
      </h1>
      <p className="mt-2 text-sm text-neutral-600">
        {mode === 'login'
          ? 'Log in to track and manage your bookings.'
          : 'Create an account to track and manage your bookings.'}
      </p>

      <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4 sm:max-w-sm">
        {mode === 'signup' && (
          <FormField
            label="Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        )}
        <FormField
          label="Email"
          type="email"
          value={contact}
          onChange={(e) => setContact(e.target.value)}
          required
        />
        <FormField
          label="Password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={error ?? undefined}
          required
        />
        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {submitting ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Sign up'}
        </button>
      </form>

      <button
        type="button"
        onClick={() => {
          setMode(mode === 'login' ? 'signup' : 'login');
          setError(null);
        }}
        className="mt-4 self-start text-sm text-neutral-600 underline"
      >
        {mode === 'login' ? "Don't have an account? Sign up" : 'Already have an account? Log in'}
      </button>
    </div>
  );
}
