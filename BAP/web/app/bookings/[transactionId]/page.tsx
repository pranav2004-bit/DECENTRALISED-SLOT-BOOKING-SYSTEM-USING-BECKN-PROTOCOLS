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
  getStatusResult,
  triggerCancel,
  triggerStatus,
} from '@/lib/booking-api';
import { ApiError } from '@/lib/api-client';
import { formatDateTime, formatPrice } from '@/lib/format';
import type { Order } from '@/lib/beckn-types';

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

  const time = order.fulfillments?.[0]?.stops?.[0]?.time.timestamp;
  const itemName = order.quote?.breakup?.[0]?.title ?? 'Service';
  const currentStatus = liveStatus ?? order.status ?? 'ACTIVE';
  const canCancel = currentStatus === 'ACTIVE' && !cancelled;

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
    </div>
  );
}
