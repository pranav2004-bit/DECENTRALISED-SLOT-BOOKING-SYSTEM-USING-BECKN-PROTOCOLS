'use client';

import { useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'next/navigation';
import Link from 'next/link';
import { LoadingState } from '@/components/ui/LoadingState';
import { EmptyState } from '@/components/ui/EmptyState';
import { BookingFailedError } from '@/components/ui/BookingErrorStates';
import {
  getCancelResult,
  getConfirmResult,
  getRatingResult,
  getStatusResult,
  getSupportResult,
  getTrackResult,
  getUpdateResult,
  triggerCancel,
  triggerRating,
  triggerStatus,
  triggerSupport,
  triggerTrack,
  triggerUpdate,
} from '@/lib/booking-api';
import { ApiError } from '@/lib/api-client';
import { formatDateTime, formatOrderItemName, formatPrice } from '@/lib/format';
import type { Order, Support } from '@/lib/beckn-types';

const STATUS_LABEL: Record<string, string> = {
  HELD: 'Held',
  ACTIVE: 'Confirmed',
  COMPLETE: 'Completed',
  CANCELLED: 'Cancelled',
};

async function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function BookingStatusPage() {
  const params = useParams<{ transactionId: string }>();
  const searchParams = useSearchParams();
  const transactionId = params.transactionId;
  const providerNameFromQuery = searchParams.get('provider_name') ?? '';

  const [order, setOrder] = useState<Order | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const [refreshingStatus, setRefreshingStatus] = useState(false);

  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);

  const [showRescheduleForm, setShowRescheduleForm] = useState(false);
  const [rescheduleTime, setRescheduleTime] = useState('');
  const [rescheduling, setRescheduling] = useState(false);
  const [rescheduleError, setRescheduleError] = useState<string | null>(null);
  const [rescheduled, setRescheduled] = useState(false);

  const [rescheduledTime, setRescheduledTime] = useState<string | null>(null);

  const [ratingValue, setRatingValue] = useState<number | null>(null);
  const [ratingSubmitting, setRatingSubmitting] = useState(false);
  const [ratingError, setRatingError] = useState<string | null>(null);
  const [ratingSubmitted, setRatingSubmitted] = useState(false);
  const [showRatingForm, setShowRatingForm] = useState(true);

  const [supportRequesting, setSupportRequesting] = useState(false);
  const [supportError, setSupportError] = useState<string | null>(null);
  const [supportContact, setSupportContact] = useState<Support | null>(null);

  const [trackChecking, setTrackChecking] = useState(false);
  const [trackError, setTrackError] = useState<string | null>(null);
  const [trackStatus, setTrackStatus] = useState<'active' | 'inactive' | null>(null);

  useEffect(() => {
    let cancelledEffect = false;
    async function load() {
      try {
        const result = await getConfirmResult(transactionId);
        if (cancelledEffect) return;
        if (!result.confirmed_order) {
          setLoadError('No confirmed booking found for this reference.');
        } else {
          setOrder(result.confirmed_order);
        }
      } catch (err) {
        if (cancelledEffect) return;
        setLoadError(err instanceof ApiError ? err.message : 'Could not load this booking');
      } finally {
        if (!cancelledEffect) setLoading(false);
      }
    }
    load();
    return () => {
      cancelledEffect = true;
    };
  }, [transactionId]);

  useEffect(() => {
    if (!order) return;
    let cancelledEffect = false;
    async function refreshStatus() {
      setRefreshingStatus(true);
      try {
        await triggerStatus(transactionId);
        for (let attempt = 0; attempt < 8 && !cancelledEffect; attempt++) {
          const result = await getStatusResult(transactionId);
          if (result.status_order?.status) {
            setLiveStatus(result.status_order.status);
            return;
          }
          if (result.status_error) return;
          await sleep(1200);
        }
      } catch {
        // A live status refresh failing isn't fatal — the authoritative
        // confirmed_order already fetched above answers "what was booked,
        // when, and with whom"; the customer just doesn't get a freshness
        // bump this time, not surfaced as a page-level error.
      } finally {
        if (!cancelledEffect) setRefreshingStatus(false);
      }
    }
    refreshStatus();
    return () => {
      cancelledEffect = true;
    };
  }, [order, transactionId]);

  async function handleCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await triggerCancel(transactionId);
      for (let attempt = 0; attempt < 15; attempt++) {
        const result = await getCancelResult(transactionId);
        if (result.cancelled_order) {
          setCancelled(true);
          setLiveStatus('CANCELLED');
          return;
        }
        if (result.cancelled_error) {
          setCancelError(result.cancelled_error.message);
          return;
        }
        await sleep(1200);
      }
      setCancelError('Cancellation is taking longer than expected — please try again.');
    } catch (err) {
      setCancelError(err instanceof ApiError ? err.message : 'Could not cancel this booking');
    } finally {
      setCancelling(false);
    }
  }

  async function handleReschedule() {
    if (!rescheduleTime) return;
    setRescheduling(true);
    setRescheduleError(null);
    try {
      // Wire shape is ISO 8601 — `datetime-local` gives seconds-less local time,
      // so a plain `new Date(...)` round-trip is enough to get a real offset.
      const isoTimestamp = new Date(rescheduleTime).toISOString();
      await triggerUpdate(transactionId, isoTimestamp);
      for (let attempt = 0; attempt < 15; attempt++) {
        const result = await getUpdateResult(transactionId);
        if (result.updated_order) {
          // dispatch_on_update's own resolved_order carries no `quote` (it isn't
          // a re-quote, just a slot move) — only the real new fulfillment
          // time(s) are trustworthy here, so only that is applied, not a
          // wholesale replacement of the already-loaded order display.
          const newTime = result.updated_order.fulfillments?.[0]?.stops?.[0]?.time.timestamp;
          if (newTime) {
            setRescheduledTime(newTime);
            setLiveStatus('ACTIVE');
          }
          setRescheduled(true);
          setShowRescheduleForm(false);
          return;
        }
        if (result.updated_error) {
          setRescheduleError(result.updated_error.message);
          return;
        }
        await sleep(1200);
      }
      setRescheduleError('Rescheduling is taking longer than expected — please try again.');
    } catch (err) {
      setRescheduleError(err instanceof ApiError ? err.message : 'Could not reschedule this booking');
    } finally {
      setRescheduling(false);
    }
  }

  async function handleSubmitRating(value: number) {
    setRatingValue(value);
    setRatingSubmitting(true);
    setRatingError(null);
    try {
      await triggerRating(transactionId, 'Order', String(value));
      for (let attempt = 0; attempt < 15; attempt++) {
        const result = await getRatingResult(transactionId);
        if (result.rating_result) {
          setRatingSubmitted(true);
          setShowRatingForm(false);
          return;
        }
        if (result.rating_error) {
          setRatingError(result.rating_error.message);
          return;
        }
        await sleep(1200);
      }
      setRatingError('Submitting your rating is taking longer than expected — please try again.');
    } catch (err) {
      setRatingError(err instanceof ApiError ? err.message : 'Could not submit your rating');
    } finally {
      setRatingSubmitting(false);
    }
  }

  async function handleRequestSupport() {
    setSupportRequesting(true);
    setSupportError(null);
    try {
      await triggerSupport(transactionId);
      for (let attempt = 0; attempt < 15; attempt++) {
        const result = await getSupportResult(transactionId);
        if (result.support_result) {
          setSupportContact(result.support_result);
          return;
        }
        if (result.support_error) {
          setSupportError(result.support_error.message);
          return;
        }
        await sleep(1200);
      }
      setSupportError('Getting support info is taking longer than expected — please try again.');
    } catch (err) {
      setSupportError(err instanceof ApiError ? err.message : 'Could not request support');
    } finally {
      setSupportRequesting(false);
    }
  }

  async function handleCheckTracking() {
    setTrackChecking(true);
    setTrackError(null);
    try {
      await triggerTrack(transactionId);
      for (let attempt = 0; attempt < 15; attempt++) {
        const result = await getTrackResult(transactionId);
        if (result.tracking) {
          setTrackStatus(result.tracking.status ?? 'inactive');
          return;
        }
        if (result.tracking_error) {
          setTrackError(result.tracking_error.message);
          return;
        }
        await sleep(1200);
      }
      setTrackError('Checking tracking status is taking longer than expected — please try again.');
    } catch (err) {
      setTrackError(err instanceof ApiError ? err.message : 'Could not check tracking status');
    } finally {
      setTrackChecking(false);
    }
  }

  if (loading) {
    return <LoadingState label="Loading your booking…" />;
  }

  if (loadError || !order) {
    return (
      <EmptyState
        title="Booking not found"
        description={loadError ?? 'No confirmed booking found for this reference.'}
        action={
          <Link href="/search" className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white">
            Back to search
          </Link>
        }
      />
    );
  }

  const time = rescheduledTime ?? order.fulfillments?.[0]?.stops?.[0]?.time.timestamp;
  const itemName = formatOrderItemName(order.quote, 'Service');
  const currentStatus = liveStatus ?? order.status ?? 'ACTIVE';
  const canCancel = currentStatus === 'ACTIVE' && !cancelled;
  const canReschedule = currentStatus === 'ACTIVE' && !cancelled;

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Your booking</h1>

      <div className="mt-6 flex flex-col gap-4 rounded-lg border border-neutral-200 p-4">
        <div className="flex items-center justify-between">
          <p className="font-medium text-neutral-900">{itemName}</p>
          <span className="rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-medium text-neutral-700">
            {STATUS_LABEL[currentStatus] ?? currentStatus}
          </span>
        </div>
        {providerNameFromQuery && <p className="text-sm text-neutral-600">{providerNameFromQuery}</p>}
        <p className="text-sm text-neutral-900">{formatDateTime(time)}</p>
        {order.quote && (
          <p className="text-sm font-semibold text-neutral-900">{formatPrice(order.quote.price)}</p>
        )}
        <p className="text-xs text-neutral-500">Booking reference: {order.id ?? transactionId}</p>
        {refreshingStatus && (
          <p className="text-xs text-neutral-400" aria-live="polite">
            Checking latest status…
          </p>
        )}
      </div>

      {cancelError && (
        <div className="mt-4">
          <BookingFailedError onRetry={handleCancel} />
        </div>
      )}

      {cancelled && (
        <p role="status" className="mt-4 text-sm text-neutral-900">
          This booking has been cancelled.
        </p>
      )}

      {canCancel && !cancelError && (
        <button
          type="button"
          onClick={handleCancel}
          disabled={cancelling}
          className="mt-6 self-start rounded-md border border-red-300 px-4 py-2 text-sm text-red-700 disabled:opacity-50"
        >
          {cancelling ? 'Cancelling…' : 'Cancel booking'}
        </button>
      )}

      {rescheduled && (
        <p role="status" className="mt-4 text-sm text-neutral-900">
          This booking has been rescheduled.
        </p>
      )}

      {canReschedule && !rescheduled && (
        <div className="mt-4 flex flex-col gap-3 rounded-lg border border-neutral-200 p-4">
          {!showRescheduleForm ? (
            <button
              type="button"
              onClick={() => setShowRescheduleForm(true)}
              className="self-start rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700"
            >
              Reschedule
            </button>
          ) : (
            <>
              <label htmlFor="reschedule-time" className="text-sm font-medium text-neutral-900">
                New date and time
              </label>
              <input
                id="reschedule-time"
                type="datetime-local"
                value={rescheduleTime}
                onChange={(e) => setRescheduleTime(e.target.value)}
                className="rounded-md border border-neutral-300 px-3 py-2 text-sm"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={handleReschedule}
                  disabled={rescheduling || !rescheduleTime}
                  className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
                >
                  {rescheduling ? 'Rescheduling…' : 'Confirm new time'}
                </button>
                <button
                  type="button"
                  onClick={() => setShowRescheduleForm(false)}
                  disabled={rescheduling}
                  className="rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
              {rescheduleError && <BookingFailedError onRetry={handleReschedule} />}
            </>
          )}
        </div>
      )}

      {currentStatus === 'COMPLETE' && (
        <div className="mt-6 flex flex-col gap-3 rounded-lg border border-neutral-200 p-4">
          <p className="font-medium text-neutral-900">Rate this booking</p>
          {ratingSubmitted && !showRatingForm ? (
            <div className="flex items-center justify-between">
              <p className="text-sm text-neutral-700">
                Thanks — you rated this {ratingValue} {ratingValue === 1 ? 'star' : 'stars'}.
              </p>
              <button
                type="button"
                onClick={() => setShowRatingForm(true)}
                className="text-sm text-neutral-600 underline"
              >
                Change rating
              </button>
            </div>
          ) : (
            <>
              <div className="flex gap-2" role="group" aria-label="Rate this booking">
                {[1, 2, 3, 4, 5].map((n) => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => handleSubmitRating(n)}
                    disabled={ratingSubmitting}
                    aria-label={`Rate ${n} ${n === 1 ? 'star' : 'stars'}`}
                    className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 disabled:opacity-50"
                  >
                    {n} ★
                  </button>
                ))}
              </div>
              {ratingSubmitting && (
                <p className="text-xs text-neutral-500" aria-live="polite">
                  Submitting…
                </p>
              )}
              {ratingError && (
                <BookingFailedError
                  onRetry={() => ratingValue !== null && handleSubmitRating(ratingValue)}
                />
              )}
            </>
          )}
        </div>
      )}

      <div className="mt-6 flex flex-col gap-3 rounded-lg border border-neutral-200 p-4">
        <div className="flex items-center justify-between gap-4">
          <p className="font-medium text-neutral-900">Need help?</p>
          {!supportContact && (
            <button
              type="button"
              onClick={handleRequestSupport}
              disabled={supportRequesting}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 disabled:opacity-50"
            >
              {supportRequesting ? 'Requesting…' : 'Get support'}
            </button>
          )}
        </div>
        {supportContact && (
          <div className="text-sm text-neutral-700">
            {supportContact.email && <p>Email: {supportContact.email}</p>}
            {supportContact.phone && <p>Phone: {supportContact.phone}</p>}
            {supportContact.url && <p>More info: {supportContact.url}</p>}
            {!supportContact.email && !supportContact.phone && !supportContact.url && (
              <p>No contact info is available for this booking yet.</p>
            )}
          </div>
        )}
        {supportError && <BookingFailedError onRetry={handleRequestSupport} />}
      </div>

      <div className="mt-6 flex flex-col gap-3 rounded-lg border border-neutral-200 p-4">
        <div className="flex items-center justify-between gap-4">
          <p className="font-medium text-neutral-900">Tracking</p>
          <button
            type="button"
            onClick={handleCheckTracking}
            disabled={trackChecking}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 disabled:opacity-50"
          >
            {trackChecking ? 'Checking…' : 'Check status'}
          </button>
        </div>
        {trackStatus && (
          <p className="text-sm text-neutral-700">
            {trackStatus === 'active'
              ? 'Active — this booking’s fulfillment is currently in progress.'
              : 'No live tracking update to show right now.'}
          </p>
        )}
        {trackError && <BookingFailedError onRetry={handleCheckTracking} />}
      </div>
    </div>
  );
}
