'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { EmptyState } from '@/components/ui/EmptyState';
import { BookingFailedError, SlotUnavailableError } from '@/components/ui/BookingErrorStates';
import { usePoll } from '@/lib/usePoll';
import { getConfirmResult, getInitResult, triggerConfirm, triggerInit } from '@/lib/booking-api';
import { ApiError } from '@/lib/api-client';
import { formatDateTime, formatPrice } from '@/lib/format';

function parseTtlSeconds(ttl: string | undefined): number | null {
  if (!ttl) return null;
  const match = /^PT(\d+)S$/.exec(ttl);
  return match ? Number(match[1]) : null;
}

function newIdempotencyKey(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

function ConfirmPageInner() {
  const params = useSearchParams();
  const router = useRouter();
  const transactionId = params.get('transaction_id');
  const itemId = params.get('item_id') ?? '';
  const itemName = params.get('item_name') ?? 'this service';
  const providerName = params.get('provider_name') ?? '';

  const [initRetryNonce, setInitRetryNonce] = useState(0);
  const initTriggeredForKeyRef = useRef<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);

  const [confirming, setConfirming] = useState(false);
  const [confirmTriggerError, setConfirmTriggerError] = useState<string | null>(null);
  const [confirmAttemptKey, setConfirmAttemptKey] = useState<string | null>(null);

  const initPollKey = transactionId ? `${transactionId}:init:${initRetryNonce}` : null;
  const {
    data: initData,
    error: initPollError,
    loading: initLoading,
  } = usePoll(
    initPollKey,
    () => getInitResult(transactionId as string),
    (result) => Boolean(result.init_order || result.init_error),
    { intervalMs: 1200, maxAttempts: 20 }
  );

  const ttlSeconds = parseTtlSeconds(initData?.init_order?.quote?.ttl);
  const [countdownStartedFor, setCountdownStartedFor] = useState<number | null>(null);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  // Starts the countdown exactly once per real hold — a fresh render-phase
  // reset (not an effect) the instant a new `ttlSeconds` value first arrives.
  if (ttlSeconds !== null && countdownStartedFor !== ttlSeconds) {
    setCountdownStartedFor(ttlSeconds);
    setSecondsLeft(ttlSeconds);
  }

  useEffect(() => {
    if (!transactionId || !initPollKey) return;
    if (initTriggeredForKeyRef.current === initPollKey) return;
    initTriggeredForKeyRef.current = initPollKey;
    triggerInit(transactionId).catch(() => {
      // A trigger-time failure (Gateway unreachable/NACK) simply leaves
      // init_order/init_error null forever — the poll's own maxAttempts
      // timeout below is what surfaces that to the customer as a real error,
      // no separate error path needed here.
    });
  }, [transactionId, initPollKey]);

  useEffect(() => {
    if (countdownStartedFor === null) return;
    const interval = setInterval(() => {
      setSecondsLeft((s) => (s === null ? null : Math.max(0, s - 1)));
    }, 1000);
    return () => clearInterval(interval);
  }, [countdownStartedFor]);

  const confirmPollKey = confirmAttemptKey;
  const {
    data: confirmData,
    error: confirmPollError,
    loading: confirmPollLoading,
  } = usePoll(
    confirmPollKey,
    () => getConfirmResult(transactionId as string),
    (result) => Boolean(result.confirmed_order || result.confirmed_error),
    { intervalMs: 1200, maxAttempts: 20 }
  );

  useEffect(() => {
    if (confirmData?.confirmed_order && transactionId) {
      const search = providerName ? `?provider_name=${encodeURIComponent(providerName)}` : '';
      router.push(`/bookings/${transactionId}${search}`);
    }
  }, [confirmData?.confirmed_order, transactionId, providerName, router]);

  if (!transactionId) {
    return (
      <EmptyState
        title="Missing order details"
        description="Start a new search to book a service."
        action={
          <Link href="/search" className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white">
            Back to search
          </Link>
        }
      />
    );
  }

  function selectAnotherTime() {
    router.push(
      `/select?transaction_id=${encodeURIComponent(transactionId as string)}&item_id=${encodeURIComponent(
        itemId
      )}&item_name=${encodeURIComponent(itemName)}&provider_name=${encodeURIComponent(providerName)}`
    );
  }

  async function handleConfirm() {
    setConfirming(true);
    setConfirmTriggerError(null);
    try {
      await triggerConfirm(transactionId as string, idempotencyKey);
      setConfirmAttemptKey(`${transactionId}:${idempotencyKey}`);
    } catch (err) {
      setConfirmTriggerError(err instanceof ApiError ? err.message : 'Could not confirm this booking');
    } finally {
      setConfirming(false);
    }
  }

  function retryConfirmAfterFailure() {
    setConfirmAttemptKey(null);
    setIdempotencyKey(newIdempotencyKey());
  }

  const order = initData?.init_order;
  const time = order?.fulfillments?.[0]?.stops?.[0]?.time.timestamp;

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Review your order</h1>

      {initPollError && (
        <ErrorState
          title="Couldn't prepare your order"
          description={initPollError.message}
          onRetry={() => setInitRetryNonce((n) => n + 1)}
        />
      )}

      {!initPollError && initLoading && !order && !initData?.init_error && (
        <LoadingState label="Preparing your order…" />
      )}

      {!initPollError && initData?.init_error && (
        <div className="mt-4">
          {initData.init_error.code === 'SLOT_UNAVAILABLE' ? (
            <SlotUnavailableError onChooseAnother={selectAnotherTime} />
          ) : (
            <ErrorState
              title="Couldn't prepare your order"
              description={initData.init_error.message}
              onRetry={() => setInitRetryNonce((n) => n + 1)}
            />
          )}
        </div>
      )}

      {!initPollError && order && !confirmAttemptKey && (
        <div className="mt-6 flex flex-col gap-4">
          <div className="rounded-lg border border-neutral-200 p-4">
            <p className="font-medium text-neutral-900">{itemName}</p>
            <p className="text-sm text-neutral-600">{providerName}</p>
            <p className="mt-2 text-sm text-neutral-900">{formatDateTime(time)}</p>
            {order.quote && (
              <dl className="mt-4 flex flex-col gap-1 border-t border-neutral-200 pt-3 text-sm">
                {order.quote.breakup.map((line) => (
                  <div key={line.title} className="flex justify-between">
                    <dt className="text-neutral-600">{line.title}</dt>
                    <dd className="text-neutral-900">{formatPrice(line.price)}</dd>
                  </div>
                ))}
                <div className="mt-1 flex justify-between border-t border-neutral-200 pt-2 font-medium">
                  <dt>Total</dt>
                  <dd>{formatPrice(order.quote.price)}</dd>
                </div>
              </dl>
            )}
            {secondsLeft !== null && (
              <p className="mt-3 text-xs text-neutral-500" aria-live="polite">
                {secondsLeft > 0
                  ? `Hold expires in ${secondsLeft}s — confirm before then.`
                  : 'This hold may have expired — confirm now to check.'}
              </p>
            )}
          </div>

          {confirmTriggerError && (
            <p role="alert" className="text-sm text-red-600">
              {confirmTriggerError}
            </p>
          )}

          <button
            type="button"
            onClick={handleConfirm}
            disabled={confirming}
            className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white disabled:opacity-50"
          >
            {confirming ? 'Confirming…' : 'Confirm booking'}
          </button>
        </div>
      )}

      {confirmAttemptKey && confirmPollError && (
        <ErrorState
          title="Couldn't confirm this booking"
          description={confirmPollError.message}
          onRetry={retryConfirmAfterFailure}
        />
      )}

      {confirmAttemptKey && !confirmPollError && confirmPollLoading && !confirmData?.confirmed_error && (
        <LoadingState label="Confirming your booking…" />
      )}

      {confirmAttemptKey && confirmData?.confirmed_error && (
        <div className="mt-4">
          {confirmData.confirmed_error.code === 'SLOT_UNAVAILABLE' ? (
            <SlotUnavailableError onChooseAnother={selectAnotherTime} />
          ) : (
            <BookingFailedError onRetry={retryConfirmAfterFailure} />
          )}
        </div>
      )}

      {confirmData?.confirmed_order && (
        <div className="mt-6 flex flex-col items-center gap-3 text-center">
          <p className="text-sm font-medium text-neutral-900">Booking confirmed! Redirecting…</p>
          <Link href={`/bookings/${transactionId}`} className="text-sm underline">
            View your booking
          </Link>
        </div>
      )}
    </div>
  );
}

export default function ConfirmPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <ConfirmPageInner />
    </Suspense>
  );
}
