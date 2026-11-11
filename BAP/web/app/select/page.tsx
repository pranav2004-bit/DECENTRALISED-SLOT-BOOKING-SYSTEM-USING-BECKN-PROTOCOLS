'use client';

import { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { formatDateTime, formatPrice } from '@/lib/format';
import { triggerInit, getInitResult, triggerConfirm, getConfirmResult } from '@/lib/booking-api';
import { ApiError } from '@/lib/api-client';

/** Client-side validation is defense-in-depth and UX only — it is NOT the
 * security boundary. Whatever endpoint Confirm eventually submits to MUST
 * independently re-validate every one of these fields server-side (never
 * trust a client); these checks exist to catch obvious bad input early and
 * give a clear, specific message instead of a generic browser tooltip.
 * React escapes all of these values wherever they're rendered (no
 * dangerouslySetInnerHTML anywhere in this app) — stored/reflected XSS via
 * this form is not a concern on the rendering side either way. */

const NAME_MAX_LENGTH = 100;
// \p{L} (Unicode "Letter") rather than [A-Za-z] — a real name field for an
// India-wide platform must accept non-ASCII names, not just English ones.
const NAME_PATTERN = /^[\p{L}][\p{L}\s'.-]{1,99}$/u;

function validateName(raw: string): string | null {
  const value = raw.trim();
  if (!value) return 'Enter your name';
  if (value.length > NAME_MAX_LENGTH) return `Name must be ${NAME_MAX_LENGTH} characters or fewer`;
  if (!NAME_PATTERN.test(value)) return 'Enter a valid name';
  return null;
}

const EMAIL_MAX_LENGTH = 254; // RFC 5321 §4.5.3.1.3
// The same pragmatic pattern the HTML5 spec itself defines for type="email"
// (whatwg.org/html — "valid e-mail address" production) — reused here so the
// JS-level check agrees with native validation instead of silently drifting
// from it, while still giving a real custom error message.
const EMAIL_PATTERN =
  /^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$/;

function validateEmail(raw: string): string | null {
  const value = raw.trim();
  if (!value) return 'Enter your email ID';
  if (value.length > EMAIL_MAX_LENGTH) return 'That email address is too long';
  if (!EMAIL_PATTERN.test(value)) return 'Enter a valid email ID';
  return null;
}

// India-only platform (same ALL_INDIA_STATES scope used across this app) —
// a real 10-digit mobile number per the TRAI numbering plan, optionally
// written with a +91/0 STD-style prefix or internal spacing/dashes, which
// this strips before checking rather than rejecting on formatting alone.
const PHONE_PATTERN = /^[6-9]\d{9}$/;

function normalizePhone(raw: string): string {
  return raw.replace(/[\s-]/g, '').replace(/^(\+91|91|0)/, '');
}

function validatePhone(raw: string): string | null {
  const value = normalizePhone(raw.trim());
  if (!value) return 'Enter your phone number';
  if (!PHONE_PATTERN.test(value)) return 'Enter a valid 10-digit mobile number';
  return null;
}

type FieldErrors = { name: string | null; email: string | null; phone: string | null };

/** Polls `fetchFn` until `isDone` is true, same interval/backoff-free shape as
 * this app's own `usePoll` hook but callable from a plain async function —
 * handleConfirm below drives three sequential real Beckn actions (select ->
 * init -> confirm), each with its own async on_X callback, from one click; a
 * React hook tied to component re-renders doesn't fit a sequential chain like
 * that as cleanly as a plain awaited loop does. */
async function pollUntil<T>(
  fetchFn: () => Promise<T>,
  isDone: (result: T) => boolean,
  { intervalMs = 1200, maxAttempts = 20 }: { intervalMs?: number; maxAttempts?: number } = {}
): Promise<T> {
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    const result = await fetchFn();
    if (isDone(result)) return result;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('This is taking longer than expected — please try again.');
}

function newIdempotencyKey(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

function SelectPageInner() {
  const params = useSearchParams();
  const router = useRouter();
  const transactionId = params.get('transaction_id');
  const itemId = params.get('item_id');
  const providerId = params.get('provider_id');
  const itemName = params.get('item_name') ?? 'this service';
  const providerName = params.get('provider_name') ?? '';
  const slotStart = params.get('slot_start');
  const priceValue = params.get('price_value');
  const priceCurrency = params.get('price_currency');
  // The real hold was already created the instant this slot was tapped on
  // the search-results card (search/page.tsx's own handleSlotTap) — this
  // page never re-selects it, only carries that hold's own real expiry
  // forward so it can keep enforcing (and showing) the same countdown.
  const holdExpiresAtParam = params.get('hold_expires_at');
  const holdExpiresAt = holdExpiresAtParam ? Number(holdExpiresAtParam) : null;

  // The customer already picked the exact slot on the search-results card
  // (its own inline BookMyShow-style selector) — this page is the one-screen
  // confirmation that follows: a booking summary, then (once Next is tapped)
  // the customer's own details revealed below the same card — never a
  // full-screen swap, and the card itself never shrinks or changes.
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [customerName, setCustomerName] = useState('');
  const [customerEmail, setCustomerEmail] = useState('');
  const [customerPhone, setCustomerPhone] = useState('');
  // Errors only show once a field has actually been touched (blurred) or a
  // submit was attempted — validating eagerly on every keystroke before the
  // user has even finished typing is a well-known anti-pattern that makes a
  // form feel like it's scolding you.
  const [errors, setErrors] = useState<FieldErrors>({ name: null, email: null, phone: null });
  const [touched, setTouched] = useState({ name: false, email: false, phone: false });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Once the real backend chain (select -> init -> confirm) genuinely
  // succeeds, this page hands off to /payment immediately (handleConfirm
  // below) rather than showing a success state itself — a booking isn't
  // actually done from the customer's side until payment has too.
  const [redirectingToPayment, setRedirectingToPayment] = useState(false);
  // Real enforcement, not just a display — this is this page's own copy of
  // the exact same expiring hold the search card started (same `expiresAt`
  // epoch-ms timestamp, this browser's own clock throughout). Reaching zero
  // here means the real backend hold is gone too (BPP's own Redis TTL), so
  // this page stops being able to do anything useful and sends the customer
  // back rather than let them keep filling in a form for a slot that's no
  // longer theirs.
  const [secondsLeft, setSecondsLeft] = useState<number | null>(() =>
    holdExpiresAt ? Math.max(0, Math.ceil((holdExpiresAt - Date.now()) / 1000)) : null
  );
  const [expired, setExpired] = useState(() => holdExpiresAt !== null && holdExpiresAt <= Date.now());

  // Backgrounded-tab edge case (found live, production build, 2026-11-04 —
  // same root cause as search/page.tsx's own identical countdown): a
  // throttled or suspended hidden-tab setInterval must not be the only way
  // this notices a lapsed hold — the visibilitychange listener recomputes
  // from the real wall-clock deadline the instant this page is looked at
  // again, same self-correcting pattern as the search card.
  useEffect(() => {
    if (!holdExpiresAt || expired || redirectingToPayment) return;
    function tick() {
      const remaining = Math.max(0, Math.ceil((holdExpiresAt! - Date.now()) / 1000));
      setSecondsLeft(remaining);
      if (remaining <= 0) setExpired(true);
    }
    const interval = setInterval(tick, 1000);
    function onVisible() {
      if (document.visibilityState === 'visible') tick();
    }
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [holdExpiresAt, expired, redirectingToPayment]);

  useEffect(() => {
    if (!expired) return;
    const timer = setTimeout(() => router.push('/search'), 1800);
    return () => clearTimeout(timer);
  }, [expired, router]);

  function validateField(field: keyof FieldErrors, value: string) {
    const message =
      field === 'name' ? validateName(value) : field === 'email' ? validateEmail(value) : validatePhone(value);
    setErrors((prev) => ({ ...prev, [field]: message }));
    return message;
  }

  if (!transactionId || !itemId || !providerId || !slotStart) {
    return (
      <EmptyState
        title="Missing selection details"
        description="Start a new search to choose a service."
        action={
          <Link href="/search" className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white">
            Back to search
          </Link>
        }
      />
    );
  }

  if (expired) {
    return (
      <EmptyState
        title="Your hold expired"
        description="This slot wasn't confirmed in time, so it's been released back for others. Taking you back to pick a time…"
        action={
          <Link href="/search" className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white">
            Back to search
          </Link>
        }
      />
    );
  }

  const fee = priceValue && priceCurrency ? formatPrice({ value: priceValue, currency: priceCurrency }) : null;

  // A real Beckn error, not a generic one. SLOT_UNAVAILABLE covers two
  // genuinely different outcomes the backend's own atomic Slot/Booking state
  // machine distinguishes by message text (real gap found live 2026-11-11:
  // collapsing both into "someone else just booked this" told a customer
  // whose own hold simply timed out mid-checkout that a stranger beat them to
  // it, which isn't what happened and isn't actionable the same way) — a
  // timed-out hold just needs a fresh selection, not "try a different slot".
  function errorMessageFor(code: string | undefined, fallback: string, message: string): string {
    if (code === 'SLOT_UNAVAILABLE') {
      if (message.includes('hold is no longer active') || message.includes('hold has already expired')) {
        return "Your hold on this slot timed out. Please go back and select it again — it's usually still open.";
      }
      return 'Someone else just booked this exact time. Please go back and pick another slot.';
    }
    return message || fallback;
  }

  async function handleConfirm(e: React.FormEvent) {
    e.preventDefault();
    // Unreachable in practice — the component's own early EmptyState return
    // above already guarantees transactionId is non-null before this form
    // (and the button that calls this) ever renders. TypeScript can't see
    // across that closure boundary, so this narrows it explicitly instead of
    // an `as string` cast — same defensive-guard pattern search/page.tsx's
    // own handleSlotTap already uses for the identical reason.
    if (!transactionId) return;
    setTouched({ name: true, email: true, phone: true });
    const nameError = validateField('name', customerName);
    const emailError = validateField('email', customerEmail);
    const phoneError = validateField('phone', customerPhone);
    if (nameError || emailError || phoneError) return;
    if (submitting) return; // already in flight — never allow a double-submit

    setSubmitting(true);
    setSubmitError(null);
    try {
      // The real atomic hold was already placed the instant this slot was
      // tapped on the search card (search/page.tsx's own handleSlotTap) —
      // that's the one place the actual double-booking race is decided
      // (backend's atomic Slot.capacity_remaining/Booking.HELD transition);
      // a 2nd customer attempting the same slot in that window already got
      // a real SLOT_UNAVAILABLE back there, before ever reaching this page.
      // This page's own job starts one step later: init — resolves the
      // real, final quote against the hold already placed.
      await triggerInit(transactionId);
      const initResult = await pollUntil(
        () => getInitResult(transactionId),
        (r) => Boolean(r.init_order || r.init_error)
      );
      if (initResult.init_error) {
        throw new Error(
          errorMessageFor(initResult.init_error.code, 'Could not prepare this booking', initResult.init_error.message)
        );
      }

      // confirm — the real, final HELD -> ACTIVE transition. This is the
      // actual, final point the customer's own identity is both required and
      // sent (the hold itself stayed anonymous until now) — and exactly
      // where the backend mints this booking's own unique customer_id
      // (BPP/backend/core/confirm_service.py) — never before this succeeds.
      await triggerConfirm(transactionId, newIdempotencyKey(), {
        name: customerName.trim(),
        email: customerEmail.trim().toLowerCase(),
        phone: normalizePhone(customerPhone.trim()),
      });
      const confirmResult = await pollUntil(
        () => getConfirmResult(transactionId),
        (r) => Boolean(r.confirmed_order || r.confirmed_error)
      );
      if (confirmResult.confirmed_error) {
        throw new Error(
          errorMessageFor(
            confirmResult.confirmed_error.code,
            'Could not confirm this booking',
            confirmResult.confirmed_error.message
          )
        );
      }

      // A booking isn't actually done from the customer's side until it's
      // paid for — hand off to /payment instead of showing a success state
      // here. `setRedirectingToPayment` also freezes the hold-countdown
      // effect above (same condition it already checks `expired` for) so the
      // customer never sees an "expired" flash while this navigation is
      // in flight.
      setRedirectingToPayment(true);
      router.push(
        `/payment?${new URLSearchParams({
          transaction_id: transactionId,
          provider_name: providerName,
        }).toString()}`
      );
    } catch (err) {
      setSubmitError(
        err instanceof ApiError || err instanceof Error ? err.message : 'Could not confirm this booking'
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <button
        type="button"
        onClick={() => (detailsOpen ? setDetailsOpen(false) : router.back())}
        className="mb-4 flex items-center gap-1 self-start text-sm text-neutral-600 hover:text-neutral-900"
      >
        <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m15 6-6 6 6 6" />
        </svg>
        Back
      </button>

      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Review your booking</h1>
        {secondsLeft !== null && (
          // Deliberately no visible countdown number (product decision) — a
          // static status chip only. `secondsLeft` itself is still tracked
          // and still drives the real expiry logic below, just not rendered.
          <span className="flex-none rounded-full bg-[var(--brand-100)] px-2.5 py-1 text-xs font-semibold text-[var(--brand-700)]">
            Held
          </span>
        )}
      </div>

      <div className="mt-6 flex flex-col gap-4 rounded-xl border border-neutral-200 bg-white p-4 shadow-sm">
        <div className="flex justify-between gap-4">
          <dt className="text-sm text-neutral-500">Hospital</dt>
          <dd className="text-right text-sm font-medium text-neutral-900">{providerName}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-sm text-neutral-500">Doctor</dt>
          <dd className="text-right text-sm font-medium text-neutral-900">{itemName}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-sm text-neutral-500">Date &amp; time</dt>
          <dd className="text-right text-sm font-medium text-neutral-900">{formatDateTime(slotStart)}</dd>
        </div>
        {fee && (
          <div className="flex justify-between gap-4 border-t border-neutral-200 pt-3">
            <dt className="text-sm font-medium text-neutral-900">Fee</dt>
            <dd className="text-right text-sm font-semibold text-neutral-900">{fee}</dd>
          </div>
        )}
      </div>

      {!detailsOpen && (
        <button
          type="button"
          onClick={() => setDetailsOpen(true)}
          className="mt-6 rounded-md bg-[var(--brand-600)] px-4 py-2.5 text-sm font-semibold text-white"
        >
          Next
        </button>
      )}

      {detailsOpen && (
        <form onSubmit={handleConfirm} className="mt-6 flex flex-col gap-4">
          <h2 className="-mb-1 text-sm font-semibold text-neutral-900">Your details</h2>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="customer-name" className="text-sm font-medium text-neutral-900">
              Name
            </label>
            <input
              id="customer-name"
              type="text"
              autoComplete="name"
              maxLength={NAME_MAX_LENGTH}
              required
              aria-invalid={touched.name && Boolean(errors.name)}
              aria-describedby={touched.name && errors.name ? 'customer-name-error' : undefined}
              value={customerName}
              onChange={(e) => {
                setCustomerName(e.target.value);
                if (touched.name) validateField('name', e.target.value);
              }}
              onBlur={() => {
                setTouched((prev) => ({ ...prev, name: true }));
                validateField('name', customerName);
              }}
              className={`rounded-md border bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                touched.name && errors.name
                  ? 'border-red-400 focus:ring-red-500'
                  : 'border-neutral-300 focus:ring-[var(--brand-600)]'
              }`}
            />
            {touched.name && errors.name && (
              <p id="customer-name-error" role="alert" className="text-xs text-red-600">
                {errors.name}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="customer-email" className="text-sm font-medium text-neutral-900">
              Email ID
            </label>
            <input
              id="customer-email"
              type="email"
              autoComplete="email"
              maxLength={EMAIL_MAX_LENGTH}
              required
              aria-invalid={touched.email && Boolean(errors.email)}
              aria-describedby={touched.email && errors.email ? 'customer-email-error' : undefined}
              value={customerEmail}
              onChange={(e) => {
                setCustomerEmail(e.target.value);
                if (touched.email) validateField('email', e.target.value);
              }}
              onBlur={() => {
                setTouched((prev) => ({ ...prev, email: true }));
                validateField('email', customerEmail);
              }}
              className={`rounded-md border bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                touched.email && errors.email
                  ? 'border-red-400 focus:ring-red-500'
                  : 'border-neutral-300 focus:ring-[var(--brand-600)]'
              }`}
            />
            {touched.email && errors.email && (
              <p id="customer-email-error" role="alert" className="text-xs text-red-600">
                {errors.email}
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="customer-phone" className="text-sm font-medium text-neutral-900">
              Phone number
            </label>
            <input
              id="customer-phone"
              type="tel"
              inputMode="numeric"
              autoComplete="tel"
              maxLength={15}
              required
              aria-invalid={touched.phone && Boolean(errors.phone)}
              aria-describedby={touched.phone && errors.phone ? 'customer-phone-error' : undefined}
              value={customerPhone}
              onChange={(e) => {
                setCustomerPhone(e.target.value);
                if (touched.phone) validateField('phone', e.target.value);
              }}
              onBlur={() => {
                setTouched((prev) => ({ ...prev, phone: true }));
                validateField('phone', customerPhone);
              }}
              className={`rounded-md border bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
                touched.phone && errors.phone
                  ? 'border-red-400 focus:ring-red-500'
                  : 'border-neutral-300 focus:ring-[var(--brand-600)]'
              }`}
            />
            {touched.phone && errors.phone && (
              <p id="customer-phone-error" role="alert" className="text-xs text-red-600">
                {errors.phone}
              </p>
            )}
          </div>
          {submitError && (
            <p role="alert" className="text-sm text-red-600">
              {submitError}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting}
            className="mt-2 rounded-md bg-[var(--brand-600)] px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            {submitting ? 'Confirming…' : 'Proceed to payment'}
          </button>
        </form>
      )}

      {redirectingToPayment && <LoadingState label="Taking you to payment…" />}
    </div>
  );
}

export default function SelectPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <SelectPageInner />
    </Suspense>
  );
}
