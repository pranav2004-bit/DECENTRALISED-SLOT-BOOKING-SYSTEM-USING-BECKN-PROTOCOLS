'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { FormField } from '@/components/ui/FormField';
import { ApiError } from '@/lib/api-client';
import { signup } from '@/lib/auth-api';
import { BUSINESS_DOMAINS } from '@/lib/constants';

/**
 * livetracker3.md §7.1: real business signup — the first half of the "built but
 * unreachable" gap this phase closes. Reuses §6.1's own domain-list decision
 * (BUSINESS_DOMAINS, the same {code, label} pairs as BAP/web's SEARCH_DOMAINS)
 * for the domain_code picker, rather than a second, independent decision.
 */
export default function SignupPage() {
  const router = useRouter();
  const [businessName, setBusinessName] = useState('');
  const [contact, setContact] = useState('');
  const [password, setPassword] = useState('');
  const [domainCode, setDomainCode] = useState(BUSINESS_DOMAINS[0].code);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await signup(businessName.trim(), contact.trim(), password, domainCode);
      router.push('/dashboard');
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create your account');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Create a business account</h1>
      <p className="mt-2 text-sm text-neutral-600">
        List your services and manage bookings on this platform.
      </p>

      <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
        <FormField
          label="Business name"
          value={businessName}
          onChange={(e) => setBusinessName(e.target.value)}
          required
        />
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
        <div className="flex flex-col gap-1.5">
          <label htmlFor="signup-domain" className="text-sm font-medium text-neutral-900">
            Category
          </label>
          <select
            id="signup-domain"
            value={domainCode}
            onChange={(e) => setDomainCode(e.target.value)}
            className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            {BUSINESS_DOMAINS.map((option) => (
              <option key={option.code} value={option.code}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
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
          {submitting ? 'Creating account…' : 'Sign up'}
        </button>
      </form>

      <Link href="/login" className="mt-4 self-start text-sm text-neutral-600 underline">
        Already have an account? Log in
      </Link>
    </div>
  );
}
