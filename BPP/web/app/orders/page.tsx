'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { EmptyState } from '@/components/ui/EmptyState';
import { me, type BusinessAccount } from '@/lib/auth-api';
import { getOrders, type Order } from '@/lib/orders-api';
import { ApiError } from '@/lib/api-client';
import { useRealtimeConnection } from '@/lib/realtime/useRealtimeConnection';

/**
 * livetracker6.md §2.2: the business-facing Orders list — a real-time read-only
 * view of confirmed bookings, live via `BusinessOrdersConsumer`
 * (`/ws/business/orders/`), backed by `GET /api/v1/orders` for the initial page
 * load. Scoped identically to that consumer: an owner sees every owned
 * resource's orders, a staff account sees only their one assigned resource's
 * own — unlike `/dashboard`, staff are NOT redirected away from this page.
 */

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: 'Confirmed',
  COMPLETE: 'Completed',
  CANCELLED: 'Cancelled',
};

function formatSlotTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function OrdersDashboard() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const loadFirstPage = useCallback(async () => {
    try {
      const result = await getOrders();
      setOrders(result.orders);
      setNextCursor(result.next_cursor);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load orders');
    }
  }, []);

  useEffect(() => {
    let ignore = false;
    getOrders()
      .then((result) => {
        if (ignore) return;
        setOrders(result.orders);
        setNextCursor(result.next_cursor);
      })
      .catch((err) => {
        if (!ignore) setLoadError(err instanceof ApiError ? err.message : 'Could not load orders');
      });
    return () => {
      ignore = true;
    };
  }, []);

  async function handleLoadMore() {
    if (!nextCursor) return;
    setLoadingMore(true);
    try {
      const result = await getOrders(nextCursor);
      setOrders((prev) => [...(prev ?? []), ...result.orders]);
      setNextCursor(result.next_cursor);
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load more orders');
    } finally {
      setLoadingMore(false);
    }
  }

  // Handled the instant a broadcast arrives, straight from useRealtimeConnection's
  // own WebSocket event listener — not via a useEffect reacting to a changed
  // lastMessage state, matching this project's own established pattern
  // (app/resources/[resourceId]/availability/page.tsx).
  const handleRealtimeMessage = useCallback((raw: unknown) => {
    if (raw == null || typeof raw !== 'object') return;
    const message = raw as { type?: string; order?: Order };
    if (message.type === 'order.confirmed' && message.order) {
      const incoming = message.order;
      setOrders((prev) => {
        const existing = prev ?? [];
        // A real refresh (e.g. a manual reload racing the live broadcast for the
        // exact same order) must not produce a visible duplicate row.
        if (existing.some((o) => o.transaction_id === incoming.transaction_id)) return existing;
        return [incoming, ...existing];
      });
    }
  }, []);

  const { status } = useRealtimeConnection('/ws/business/orders/', handleRealtimeMessage);

  if (loadError) return <ErrorState description={loadError} onRetry={loadFirstPage} />;
  if (orders === null) return <LoadingState label="Loading orders…" />;

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col gap-4 px-4 py-8 sm:px-6 lg:px-8">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Orders</h1>
        <span className="flex items-center gap-2 text-xs text-neutral-600">
          <span
            className={`h-2 w-2 rounded-full ${
              status === 'open'
                ? 'bg-green-500'
                : status === 'forbidden'
                  ? 'bg-red-500'
                  : 'bg-neutral-400'
            }`}
            aria-hidden="true"
          />
          <span aria-live="polite">
            {status === 'open' ? 'Live' : status === 'forbidden' ? 'Access ended' : 'Connecting…'}
          </span>
        </span>
      </div>

      {orders.length === 0 ? (
        <EmptyState
          title="No orders yet"
          description="Confirmed bookings for your resources will appear here live."
        />
      ) : (
        <ul className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200">
          {orders.map((order) => (
            <li key={order.transaction_id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div>
                <p className="text-sm font-medium text-neutral-900">{order.resource_name}</p>
                <p className="text-xs text-neutral-600">{formatSlotTime(order.slot_time)}</p>
              </div>
              <span className="rounded-full bg-neutral-100 px-2.5 py-0.5 text-xs font-medium text-neutral-700">
                {STATUS_LABEL[order.status] ?? order.status}
              </span>
            </li>
          ))}
        </ul>
      )}

      {nextCursor && (
        <button
          type="button"
          onClick={handleLoadMore}
          disabled={loadingMore}
          className="self-start rounded-md border border-neutral-300 px-4 py-2 text-sm text-neutral-700 disabled:opacity-50"
        >
          {loadingMore ? 'Loading…' : 'Load more'}
        </button>
      )}
    </div>
  );
}

export default function OrdersPage() {
  const router = useRouter();
  const [account, setAccount] = useState<BusinessAccount | null | 'loading'>('loading');

  useEffect(() => {
    let cancelled = false;
    me().then((result) => {
      if (cancelled) return;
      if (result === null) {
        router.push('/login');
        return;
      }
      setAccount(result);
    });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (account === 'loading' || account === null) return <LoadingState label="Checking session…" />;
  return <OrdersDashboard />;
}
