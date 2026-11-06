'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { BRAND } from '@/lib/brand';
import { SEARCH_DOMAINS } from '@/lib/constants';
import { me, type Customer } from '@/lib/auth-api';
import { listBookings, type BookingListItem } from '@/lib/bookings-api';
import { deriveBookingListStatus } from '@/lib/booking-status';
import { formatOrderItemName, formatDateTime } from '@/lib/format';

/**
 * OnSlot's real home tab — not a marketing landing page. Structured the way
 * BookMyShow/PhonePe/Paytm/Ola/Rapido actually build a home screen: a
 * personalized greeting, one search entry point, and a small category grid
 * for OnSlot's own 3 real domains (no invented sub-categories, per the
 * reference moodboard's own instruction — the domain icons already are the
 * browsing surface, so there's no second "Verified Workers Near You" list).
 * The one promo card reuses this product's own already-established, real
 * value proposition — not a fabricated "Emergency Service" claim that has
 * no equivalent in a beauty/healthcare/automotive marketplace.
 */

function SpaWavesIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 8c2-2 4-2 6 0s4 2 6 0 4-2 6 0" />
      <path d="M3 13c2-2 4-2 6 0s4 2 6 0 4-2 6 0" />
      <path d="M3 18c2-2 4-2 6 0s4 2 6 0 4-2 6 0" />
    </svg>
  );
}

function MedicalBagIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 10a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8Z" />
      <path d="M8 8V6a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M10 14h4M12 12v4" />
    </svg>
  );
}

function CarIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 11.5 6.6 7a2 2 0 0 1 1.9-1.4h7a2 2 0 0 1 1.9 1.4l1.6 4.5" />
      <rect x="3" y="11.5" width="18" height="5.5" rx="2" />
      <circle cx="7.5" cy="17.5" r="1.6" />
      <circle cx="16.5" cy="17.5" r="1.6" />
    </svg>
  );
}

function MoreIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </svg>
  );
}

const DOMAIN_ICONS: Record<string, (props: { className?: string }) => ReactNode> = {
  'ONDC:RET13': SpaWavesIcon,
  'ONDC:SRV13': MedicalBagIcon,
  'BECKN:AUTO01': CarIcon,
};

function domainHref(code: string): string {
  return `/search?domain=${encodeURIComponent(code)}`;
}

function UpcomingBookingCard({ booking }: { booking: BookingListItem }) {
  const order = booking.confirmed_order;
  const itemName = formatOrderItemName(order?.quote, 'Your booking');
  const time = order?.fulfillments?.[0]?.stops?.[0]?.time.timestamp;

  return (
    <Link
      href={`/bookings/${booking.transaction_id}`}
      className="flex items-center gap-3 rounded-2xl border border-[var(--brand-100)] bg-[var(--brand-50)] p-4 transition-colors hover:border-[var(--brand-600)]"
    >
      <span className="flex h-10 w-10 flex-none items-center justify-center rounded-full bg-[var(--brand-600)] text-white">
        <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="3" y="5" width="18" height="16" rx="2" />
          <path d="M3 10h18M8 3v4M16 3v4" />
        </svg>
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-[var(--brand-700)]">
          Upcoming booking
        </p>
        <p className="truncate text-sm font-semibold text-neutral-900">{itemName}</p>
        {time && <p className="text-xs text-neutral-600">{formatDateTime(time)}</p>}
      </div>
      <svg viewBox="0 0 24 24" className="h-4 w-4 flex-none text-neutral-400" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="m9 18 6-6-6-6" />
      </svg>
    </Link>
  );
}

export default function Home() {
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [upcoming, setUpcoming] = useState<BookingListItem | null>(null);
  const [showMore, setShowMore] = useState(false);

  useEffect(() => {
    let cancelled = false;
    me().then((c) => {
      if (cancelled) return;
      setCustomer(c);
      setCheckingSession(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!customer) {
      setUpcoming(null);
      return;
    }
    let cancelled = false;
    listBookings()
      .then(({ bookings }) => {
        if (cancelled) return;
        const now = Date.now();
        const next = bookings
          .filter((b) => deriveBookingListStatus(b) === 'ACTIVE')
          .map((b) => ({
            booking: b,
            timestamp: b.confirmed_order?.fulfillments?.[0]?.stops?.[0]?.time.timestamp,
          }))
          .filter(
            (c): c is { booking: BookingListItem; timestamp: string } =>
              !!c.timestamp && new Date(c.timestamp).getTime() > now
          )
          .sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())[0];
        setUpcoming(next?.booking ?? null);
      })
      .catch(() => {
        // A home-screen teaser failing quietly is correct here — the full
        // bookings list at /bookings already has its own real error state.
      });
    return () => {
      cancelled = true;
    };
  }, [customer]);

  const firstName = customer?.name.trim().split(/\s+/)[0];

  return (
    <>
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col gap-6 px-4 pb-8 pt-6 sm:px-6">
      <div>
        <h1 className="font-[family-name:var(--font-title)] text-2xl font-extrabold tracking-tight text-neutral-900">
          {checkingSession ? ' ' : firstName ? `Hi, ${firstName}` : `Welcome to ${BRAND.name}`}
        </h1>
        <p className="mt-1 text-sm text-neutral-600">What would you like to book today?</p>
      </div>

      {upcoming && <UpcomingBookingCard booking={upcoming} />}

      <div>
        <div className="flex items-center justify-between">
          <h2 className="font-[family-name:var(--font-title)] text-base font-bold text-neutral-900">
            Explore
          </h2>
        </div>
        <div className="mt-3 grid grid-cols-4 gap-3">
          {SEARCH_DOMAINS.map((domain) => {
            const Icon = DOMAIN_ICONS[domain.code];
            return (
              <Link
                key={domain.code}
                href={domainHref(domain.code)}
                className="flex flex-col items-center gap-2 text-center transition-opacity active:opacity-70"
              >
                <span className="flex h-14 w-14 items-center justify-center rounded-full bg-[var(--brand-100)] text-[var(--brand-700)] transition-transform active:scale-95">
                  {Icon && <Icon className="h-6 w-6" />}
                </span>
                <span className="text-xs font-semibold leading-tight text-neutral-900">
                  {domain.label}
                </span>
              </Link>
            );
          })}
          <button
            type="button"
            onClick={() => setShowMore(true)}
            className="flex flex-col items-center gap-2 text-center transition-opacity active:opacity-70"
          >
            <span className="flex h-14 w-14 items-center justify-center rounded-full bg-neutral-100 text-neutral-500 transition-transform active:scale-95">
              <MoreIcon className="h-6 w-6" />
            </span>
            <span className="text-xs font-semibold leading-tight text-neutral-900">More</span>
          </button>
        </div>
      </div>
    </div>

    {showMore && (
      <div
        role="dialog"
        aria-modal="true"
        aria-label="All categories"
        className="fixed inset-0 z-50 flex items-end justify-center bg-neutral-900/40 sm:items-center"
        onClick={() => setShowMore(false)}
      >
        <div
          className="w-full max-w-md rounded-t-2xl bg-white p-5 sm:rounded-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between">
            <h2 className="font-[family-name:var(--font-title)] text-base font-bold text-neutral-900">
              All categories
            </h2>
            <button
              type="button"
              onClick={() => setShowMore(false)}
              aria-label="Close"
              className="rounded-full p-1.5 text-neutral-500 hover:bg-neutral-100"
            >
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="m18 6-12 12M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="mt-4 grid grid-cols-3 gap-3">
            {SEARCH_DOMAINS.map((domain) => {
              const Icon = DOMAIN_ICONS[domain.code];
              return (
                <Link
                  key={domain.code}
                  href={domainHref(domain.code)}
                  onClick={() => setShowMore(false)}
                  className="flex flex-col items-center gap-2 text-center transition-opacity active:opacity-70"
                >
                  <span className="flex h-14 w-14 items-center justify-center rounded-full bg-[var(--brand-100)] text-[var(--brand-700)]">
                    {Icon && <Icon className="h-6 w-6" />}
                  </span>
                  <span className="text-xs font-semibold leading-tight text-neutral-900">
                    {domain.label}
                  </span>
                </Link>
              );
            })}
          </div>
          <p className="mt-5 text-center text-xs text-neutral-500">More domains coming soon…</p>
        </div>
      </div>
    )}
    </>
  );
}
