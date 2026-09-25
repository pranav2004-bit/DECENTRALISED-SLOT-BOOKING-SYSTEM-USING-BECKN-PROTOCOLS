'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { FormField } from '@/components/ui/FormField';
import { ApiError } from '@/lib/api-client';
import { signup } from '@/lib/auth-api';
import { BRAND } from '@/lib/brand';
import { RESOURCE_TYPES_BY_DOMAIN } from '@/lib/constants';

/**
 * livetracker3.md §7.1: real business signup — the first half of the "built but
 * unreachable" gap this phase closes.
 *
 * Category is fixed to this brand's own domain (BRAND.domainCode), not a picker —
 * each of the 3 BPP instances' backends is domain-locked (SUPPORTED_DOMAINS), so
 * letting a user choose a different category here would create an account the
 * backend can never actually serve. Was previously an editable <select> defaulting
 * to BUSINESS_DOMAINS[0] (Beauty) regardless of which brand was rendering it — a
 * real bug across all 3 instances, not just CareNest.
 */
export default function SignupPage() {
  const router = useRouter();
  const [businessName, setBusinessName] = useState('');
  const [contact, setContact] = useState('');
  const [password, setPassword] = useState('');
  const domainCode = BRAND.domainCode;
  // A domain with exactly one resource type (e.g. Healthcare -> Doctor) shows that
  // type's name as the category, since it's more specific than the generic domain
  // label. A domain with multiple resource types (Beauty: Stylist/Chair, Automotive:
  // Bay/Mechanic) falls back to the domain label, since no single resource type
  // represents the whole category.
  const resourceTypes = RESOURCE_TYPES_BY_DOMAIN[domainCode] ?? [];
  const categoryLabel = resourceTypes.length === 1 ? resourceTypes[0].label : BRAND.domainLabel;
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
      <button
        type="button"
        onClick={() => router.back()}
        className="mb-4 flex w-fit items-center gap-1.5 text-sm font-medium text-neutral-600 transition-colors hover:text-neutral-900 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 rounded"
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M15 18l-6-6 6-6" />
        </svg>
        Back
      </button>
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
          <span className="text-sm font-medium text-neutral-900">Category</span>
          <div className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-700">
            {categoryLabel}
          </div>
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
