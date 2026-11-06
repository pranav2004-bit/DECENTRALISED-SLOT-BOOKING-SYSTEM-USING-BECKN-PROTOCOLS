'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { CustomerSearchDemo } from '@/components/marketing/CustomerSearchDemo';
import { FormField } from '@/components/ui/FormField';
import { useToast } from '@/components/ui/Toast';
import { ApiError } from '@/lib/api-client';
import { signup } from '@/lib/auth-api';
import { BRAND } from '@/lib/brand';
import { capitalizeWords } from '@/lib/format';

/**
 * livetracker3.md §7.1: real business signup — the first half of the "built but
 * unreachable" gap this phase closes.
 *
 * Category is fixed to this brand's own domain (BRAND.domainCode), not a picker or
 * a display field — each of the 3 BPP instances' backends is domain-locked
 * (SUPPORTED_DOMAINS), so a business signing up through this brand's own site can
 * only ever be that brand's domain; there's nothing for the customer to choose or
 * confirm here.
 */
export default function SignupPage() {
  const router = useRouter();
  const { showToast } = useToast();
  const [businessName, setBusinessName] = useState('');
  const [contact, setContact] = useState('');
  const [password, setPassword] = useState('');
  const domainCode = BRAND.domainCode;
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await signup(capitalizeWords(businessName.trim()), contact.trim(), password, domainCode);
      showToast('Account created successfully');
      // Not /dashboard — that's a placeholder now; /resources is where a fresh
      // owner actually adds their first bookable resource.
      router.push('/resources');
      router.refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not create your account');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col bg-neutral-50">
      <div className="mx-auto grid w-full max-w-6xl flex-1 gap-12 px-4 py-8 sm:px-6 lg:grid-cols-2 lg:items-center lg:px-8">
        <div className="mx-auto w-full max-w-md rounded-2xl border border-neutral-200 bg-white p-6 shadow-sm sm:p-8">
          <Link
            href="/"
            className="mb-4 flex w-fit items-center gap-1.5 text-sm font-medium text-neutral-600 transition-colors hover:text-neutral-900 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 rounded"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M15 18l-6-6 6-6" />
            </svg>
            Back
          </Link>
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
            {error && (
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="rounded-md bg-neutral-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              {submitting ? 'Creating account…' : 'Sign up'}
            </button>
          </form>

          <p className="mt-4 self-start text-sm text-neutral-600">
            Already have an account?{' '}
            <Link href="/login" className="text-blue-600 hover:text-blue-700 hover:underline">
              Log in
            </Link>
          </p>
        </div>

        <div className="hidden lg:block">
          <h2 className="mb-4 text-center text-lg font-semibold text-neutral-900">
            {BRAND.demoWidgetHeading}
          </h2>
          <CustomerSearchDemo />
        </div>
      </div>
    </div>
  );
}
