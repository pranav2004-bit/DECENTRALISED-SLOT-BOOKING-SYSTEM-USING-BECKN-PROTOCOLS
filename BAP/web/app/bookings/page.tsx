'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { ErrorState } from '@/components/ui/ErrorState';
import { me } from '@/lib/auth-api';
import { listBookings, type BookingListItem } from '@/lib/bookings-api';
import { ApiError } from '@/lib/api-client';
import { SEARCH_DOMAINS } from '@/lib/constants';
import { formatDateTime, formatOrderItemName } from '@/lib/format';
import { BOOKING_LIST_STATUS_LABEL, deriveBookingListStatus } from '@/lib/booking-status';

/**
 * livetracker3.md §9.1: the "My Bookings" list — closes the gap where a logged-in
 * customer had no way to discover their own past bookings except already knowing a
 * specific transactionId to type into a URL by hand.
 */
function domainLabel(code: string): string {
  return SEARCH_DOMAINS.find((d) => d.code === code)?.label ?? code;
}

export default function BookingsPage() {
  const router = useRouter();
  const [checkingSession, setCheckingSession] = useState(true);
  const [bookings, setBookings] = useState<BookingListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadMoreLoading, setLoadMoreLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function loadPage(cursor: string | null) {
    const result = await listBookings(cursor);
    setBookings((prev) => (cursor ? [...prev, ...result.bookings] : result.bookings));
    setNextCursor(result.next_cursor);
  }

  useEffect(() => {
    let cancelled = false;
    async function init() {
      const customer = await me();
      if (cancelled) return;
      if (!customer) {
        router.push('/account');
        return;
      }
      setCheckingSession(false);
      try {
        await loadPage(null);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof ApiError ? err.message : 'Could not load your bookings');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    init();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleLoadMore() {
    setLoadMoreLoading(true);
    setError(null);
    try {
      await loadPage(nextCursor);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load more bookings');
    } finally {
      setLoadMoreLoading(false);
    }
  }

  if (checkingSession || loading) {
    return <LoadingState label="Loading your bookings…" />;
  }

  if (error && bookings.length === 0) {
    return <ErrorState description={error} onRetry={() => window.location.reload()} />;
  }

  if (bookings.length === 0) {
    return (
      <EmptyState
        title="No bookings yet"
        description="Once you confirm a booking, it will show up here."
        action={
          <Link href="/search" className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white">
            Find something to book
          </Link>
        }
      />
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Your bookings</h1>

      <ul className="mt-6 flex flex-col gap-3">
        {bookings.map((booking) => {
          const status = deriveBookingListStatus(booking);
          const order = booking.confirmed_order;
          const itemName = formatOrderItemName(order?.quote, 'Booking');
          const time = order?.fulfillments?.[0]?.stops?.[0]?.time.timestamp;
          return (
            <li key={booking.transaction_id}>
              <Link
                href={`/bookings/${booking.transaction_id}`}
                className="flex flex-col gap-1 rounded-lg border border-neutral-200 p-4 hover:border-neutral-400"
              >
                <div className="flex items-center justify-between gap-4">
                  <p className="font-medium text-neutral-900">{itemName}</p>
                  <span className="rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-medium text-neutral-700">
                    {BOOKING_LIST_STATUS_LABEL[status]}
                  </span>
                </div>
                <p className="text-xs text-neutral-500">{domainLabel(booking.domain)}</p>
                {time && <p className="text-sm text-neutral-600">{formatDateTime(time)}</p>}
              </Link>
            </li>
          );
        })}
      </ul>

      {error && bookings.length > 0 && (
        <p role="alert" className="mt-4 text-sm text-red-700">
          {error}
        </p>
      )}

      {nextCursor && (
        <button
          type="button"
          onClick={handleLoadMore}
          disabled={loadMoreLoading}
          className="mt-6 self-start rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 disabled:opacity-50"
        >
          {loadMoreLoading ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  );
}
