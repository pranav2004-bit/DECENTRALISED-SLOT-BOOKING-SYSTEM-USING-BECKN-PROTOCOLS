'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { FormField } from '@/components/ui/FormField';
import { ApiError } from '@/lib/api-client';
import { login } from '@/lib/auth-api';

/** livetracker3.md §7.1: real business login, wired to auth-api.ts's already-existing
 * login() — built for the Phase 4.4 dashboard's own inline login form, never reachable
 * as its own standalone page until now. */
export default function LoginPage() {
  const router = useRouter();
  const [contact, setContact] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await login(contact.trim(), password);
      router.push('/dashboard');
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not log in');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Business login</h1>

      <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
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
          required
        />
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {submitting ? 'Logging in…' : 'Log in'}
        </button>
      </form>

      <Link href="/signup" className="mt-4 self-start text-sm text-neutral-600 underline">
        Don&apos;t have an account? Sign up
      </Link>
    </div>
  );
}
