'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { EmptyState } from '@/components/ui/EmptyState';
import { usePoll } from '@/lib/usePoll';
import { useRealtimeConnection } from '@/lib/realtime/useRealtimeConnection';
import { getPaymentResult, triggerPayment, type PaymentResultResponse } from '@/lib/booking-api';
import { openRazorpayCheckout } from '@/lib/razorpay-checkout';
import { ApiError } from '@/lib/api-client';
import { formatPrice } from '@/lib/format';

/**
 * livetracker5.md Phase 3.1/3.2 — customer-facing payment trigger + status page.
 * Reached only after a real /confirm success (§3.1's own ordering requirement:
 * payment can't start before the booking + quote are final), matching the same
 * trigger/result-poll shape every other booking-flow screen in this app uses.
 */

function newIdempotencyKey(): string {
  return typeof crypto !== 'undefined' && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

type Phase = 'idle' | 'starting' | 'awaiting_checkout' | 'polling';

function PaymentPageInner() {
  const params = useSearchParams();
  const router = useRouter();
  const transactionId = params.get('transaction_id');

  const [phase, setPhase] = useState<Phase>('idle');
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [pollKey, setPollKey] = useState<string | null>(null);
  const orderRef = useRef<PaymentResultResponse | null>(null);

  const {
    data: polledData,
    error: pollError,
  } = usePoll(
    pollKey,
    () => getPaymentResult(transactionId as string),
    (result) => result.status === 'SUCCEEDED' || result.status === 'FAILED',
    { intervalMs: 1500, maxAttempts: 40 }
  );

  // livetracker5.md Phase 3.3: real-time push, with polling above as the
  // already-proven fallback — never the *only* mechanism, since a dropped
  // socket must not silently strand the customer on "Confirming your
  // payment…" forever. `useRealtimeConnection` already reconnects on its own;
  // this only reacts to a genuine status-changed message when one arrives.
  const { lastMessage } = useRealtimeConnection(
    transactionId ? `/ws/payment/${transactionId}/` : '/ws/'
  );
  const [pushedData, setPushedData] = useState<PaymentResultResponse | null>(null);
  useEffect(() => {
    if (!pollKey || !transactionId) return;
    const message = lastMessage as { type?: string; status?: string } | null;
    if (message?.type !== 'payment.status_changed') return;
    getPaymentResult(transactionId).then(setPushedData).catch(() => {
      // A transient fetch failure here is not fatal — usePoll's own next tick
      // (at most 1.5s away) still resolves the real status.
    });
  }, [lastMessage, pollKey, transactionId]);

  const paymentData = pushedData ?? polledData;
  // Merged across both sources (push + poll) — `usePoll`'s own `pollLoading`
  // only reflects the polling source, so gating the "still confirming" state
  // on that alone let a fast push (settled) and a stale `pollLoading=true`
  // render two conflicting states at once. `isSettled` is the single source
  // of truth for which block below actually renders.
  const isSettled = paymentData?.status === 'SUCCEEDED' || paymentData?.status === 'FAILED';

  useEffect(() => {
    if (paymentData?.status === 'SUCCEEDED' && transactionId) {
      router.push(`/bookings/${transactionId}`);
    }
  }, [paymentData?.status, transactionId, router]);

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

  async function startPayment() {
    setPhase('starting');
    setTriggerError(null);
    setPushedData(null);
    try {
      const result = await triggerPayment(transactionId as string, idempotencyKey);
      orderRef.current = result;

      if (result.status === 'SUCCEEDED') {
        // A network retry replayed an already-resolved transaction — no
        // checkout widget needed, go straight to polling (which resolves
        // immediately from the already-SUCCEEDED state).
        setPollKey(`${transactionId}:${idempotencyKey}`);
        setPhase('polling');
        return;
      }
      if (result.status === 'FAILED') {
        setTriggerError('This payment attempt was declined. You can try again.');
        setPhase('idle');
        return;
      }

      const keyId = process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID;
      if (!keyId) {
        setTriggerError('Payment is not configured yet — please contact support.');
        setPhase('idle');
        return;
      }

      setPhase('awaiting_checkout');
      await openRazorpayCheckout({
        keyId,
        orderId: result.vendor_txn_id,
        amountRupees: result.amount,
        currency: result.currency,
        onBelievedSuccess: () => {
          // Design Principle 5: the widget's own success callback is never
          // trusted directly — it only starts polling for the real,
          // server-verified webhook-driven status.
          setPollKey(`${transactionId}:${idempotencyKey}`);
          setPhase('polling');
        },
        onDismiss: () => {
          setTriggerError('Payment was not completed. You can try again.');
          setPhase('idle');
        },
      });
    } catch (err) {
      setTriggerError(err instanceof ApiError ? err.message : 'Could not start payment');
      setPhase('idle');
    }
  }

  function retryAfterFailure() {
    setPollKey(null);
    setPushedData(null);
    orderRef.current = null;
    setIdempotencyKey(newIdempotencyKey());
    setTriggerError(null);
    setPhase('idle');
  }

  return (
    <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-4 py-8 sm:px-6 lg:px-8">
      <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Payment</h1>

      {phase === 'idle' && !pollKey && (
        <div className="mt-6 flex flex-col gap-4">
          {triggerError && (
            <p role="alert" className="text-sm text-red-600">
              {triggerError}
            </p>
          )}
          <button
            type="button"
            onClick={startPayment}
            className="rounded-md bg-neutral-900 px-4 py-2 text-sm text-white"
          >
            Proceed to payment
          </button>
        </div>
      )}

      {(phase === 'starting' || phase === 'awaiting_checkout') && (
        <LoadingState
          label={phase === 'starting' ? 'Preparing payment…' : 'Waiting for payment…'}
        />
      )}

      {pollKey && pollError && (
        <ErrorState
          title="Couldn't confirm your payment"
          description={pollError.message}
          onRetry={retryAfterFailure}
        />
      )}

      {pollKey && !pollError && !isSettled && (
        <LoadingState label="Confirming your payment…" />
      )}

      {pollKey && paymentData?.status === 'FAILED' && (
        <ErrorState
          title="Payment declined"
          description="Your payment could not be completed. You can try again."
          onRetry={retryAfterFailure}
        />
      )}

      {pollKey && paymentData?.status === 'SUCCEEDED' && (
        <div className="mt-6 flex flex-col items-center gap-3 text-center">
          <p className="text-sm font-medium text-neutral-900">
            Payment of {formatPrice({ currency: paymentData.currency, value: paymentData.amount })}{' '}
            received. Redirecting…
          </p>
          <Link href={`/bookings/${transactionId}`} className="text-sm underline">
            View your booking
          </Link>
        </div>
      )}
    </div>
  );
}

export default function PaymentPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <PaymentPageInner />
    </Suspense>
  );
}
