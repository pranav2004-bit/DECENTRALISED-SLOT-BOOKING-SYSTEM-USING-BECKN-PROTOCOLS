'use client';

import { Suspense, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { EmptyState } from '@/components/ui/EmptyState';
import { SlotUnavailableError } from '@/components/ui/BookingErrorStates';
import { usePoll } from '@/lib/usePoll';
import { getSelectResult, triggerSelect } from '@/lib/booking-api';
import { ApiError } from '@/lib/api-client';
import { formatDateTime } from '@/lib/format';

function minDateTimeLocal(): string {
  const now = new Date(Date.now() + 5 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(
    now.getHours()
  )}:${pad(now.getMinutes())}`;
}

function SelectPageInner() {
  const params = useSearchParams();
  const transactionId = params.get('transaction_id');
  const itemId = params.get('item_id');
  const itemName = params.get('item_name') ?? 'this service';
  const providerName = params.get('provider_name') ?? '';

  const [requestedTime, setRequestedTime] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [attemptKey, setAttemptKey] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  const pollKey = attemptKey ? `${attemptKey}:${retryNonce}` : null;
  const { data, error, loading } = usePoll(
    pollKey,
    () => getSelectResult(transactionId as string),
    (result) => Boolean(result.selected_order || result.selected_error),
    { intervalMs: 1200, maxAttempts: 20 }
  );

  if (!transactionId || !itemId) {
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!requestedTime) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const isoTimestamp = new Date(requestedTime).toISOString();
      await triggerSelect({ transactionId: transactionId as string, itemId: itemId as string, requestedTimestamp: isoTimestamp });
      setAttemptKey(`${transactionId}:${itemId}:${isoTimestamp}`);
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Could not reserve this slot');
    } finally {
      setSubmitting(false);
    }
  }

  function chooseAnotherTime() {
    setAttemptKey(null);
    setRetryNonce(0);
  }

  const showForm = !attemptKey;

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Choose a time</h1>
      <p className="mt-1 text-sm text-neutral-600">
        {itemName}
        {providerName ? ` — ${providerName}` : ''}
      </p>

      {showForm && (
        <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="requested-time" className="text-sm font-medium text-neutral-900">
              Date and time
            </label>
            <input
              id="requested-time"
              type="datetime-local"
              min={minDateTimeLocal()}
              value={requestedTime}
              onChange={(e) => setRequestedTime(e.target.value)}
              required
              className="rounded-md border border-neutral-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
            />
          </div>
          {submitError && (
            <p role="alert" className="text-sm text-red-600">
              {submitError}
            </p>
          )}
          <button
            type="submit"
            disabled={submitting || !requestedTime}
            className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {submitting ? 'Reserving…' : 'Reserve this slot'}
          </button>
        </form>
      )}

      {attemptKey && loading && <LoadingState label="Checking availability…" />}

      {attemptKey && error && (
        <ErrorState
          title="Couldn't check availability"
          description={error.message}
          onRetry={() => setRetryNonce((n) => n + 1)}
        />
      )}

      {attemptKey && data?.selected_error && !loading && (
        <div className="mt-4">
          {data.selected_error.code === 'SLOT_UNAVAILABLE' ? (
            <SlotUnavailableError onChooseAnother={chooseAnotherTime} />
          ) : (
            <ErrorState
              title="Couldn't reserve this slot"
              description={data.selected_error.message}
              onRetry={chooseAnotherTime}
              actionLabel="Try again"
            />
          )}
        </div>
      )}

      {attemptKey && data?.selected_order && !loading && (
        <div className="mt-6 flex flex-col gap-4 rounded-lg border border-neutral-200 p-4">
          <p className="text-sm text-neutral-900">
            <span className="font-medium">Reserved:</span>{' '}
            {formatDateTime(data.selected_order.fulfillments?.[0]?.stops?.[0]?.time.timestamp)}
          </p>
          <Link
            href={{
              pathname: '/confirm',
              query: {
                transaction_id: transactionId,
                item_id: itemId,
                item_name: itemName,
                provider_name: providerName,
              },
            }}
            className="self-start rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
          >
            Review order
          </Link>
        </div>
      )}
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
