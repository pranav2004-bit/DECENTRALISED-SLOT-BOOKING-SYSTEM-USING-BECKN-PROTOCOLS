'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { LoadingState } from '@/components/ui/LoadingState';
import { ErrorState } from '@/components/ui/ErrorState';
import { FormField } from '@/components/ui/FormField';
import { login, me, type BusinessAccount } from '@/lib/auth-api';
import { getSlots, blockSlots, type SlotInfo, type ResourceInfo } from '@/lib/availability-api';
import { ApiError } from '@/lib/api-client';
import { useRealtimeConnection } from '@/lib/realtime/useRealtimeConnection';

/**
 * Phase 4.4 (livetracker2.md §4.4) Test Gate screen: a business owner or the one
 * staff member assigned to this Resource watches its slots live — a slot going from
 * AVAILABLE to HELD (customer holds it) or back again (release/cancel/reschedule)
 * updates here without a manual refresh, over the real WebSocket connection
 * established in §2.4. Also the "not just a one-way dashboard feed" half of the
 * checklist: the Block button sends a real client -> server `block_slot` message
 * over the same socket when it's open, falling back to the plain REST endpoint
 * (`resource_availability_block_view`) only if it isn't.
 */

const STATUS_LABEL: Record<SlotInfo['status'], string> = {
  AVAILABLE: 'Available',
  HELD: 'Held',
  BOOKED: 'Booked',
  CANCELLED: 'Blocked',
};

const STATUS_BADGE_CLASS: Record<SlotInfo['status'], string> = {
  AVAILABLE: 'bg-green-100 text-green-800',
  HELD: 'bg-amber-100 text-amber-800',
  BOOKED: 'bg-neutral-200 text-neutral-700',
  CANCELLED: 'bg-neutral-200 text-neutral-500',
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

function LoginForm({ onLoggedIn }: { onLoggedIn: (account: BusinessAccount) => void }) {
  const [contact, setContact] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const account = await login(contact, password);
      onLoggedIn(account);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Login failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto flex w-full max-w-sm flex-col gap-4 py-12">
      <h1 className="text-lg font-semibold text-neutral-900">Business login</h1>
      <FormField
        label="Contact"
        type="text"
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
        {submitting ? 'Logging in…' : 'Log in'}
      </button>
    </form>
  );
}

function AvailabilityDashboard({ resourceId }: { resourceId: string }) {
  const [resource, setResource] = useState<ResourceInfo | null>(null);
  const [slots, setSlots] = useState<SlotInfo[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [blockingId, setBlockingId] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const applySlotsResult = useCallback(
    (result: { resource: ResourceInfo; slots: SlotInfo[] } | null, err: unknown) => {
      if (result !== null) {
        setResource(result.resource);
        setSlots(result.slots);
        setLoadError(null);
      } else {
        setLoadError(err instanceof ApiError ? err.message : 'Could not load this resource');
      }
    },
    []
  );

  // React's own recommended shape for a data-fetching effect (react.dev/learn/you-
  // might-not-need-an-effect's "Fetching data" example): the state-setting calls live
  // inside the `.then()`/`.catch()` callbacks, not as a bare statement at the effect
  // body's top level, plus an `ignore` flag so a stale response from an old `resourceId`
  // can never clobber a newer one's state.
  useEffect(() => {
    let ignore = false;
    getSlots(resourceId)
      .then((result) => {
        if (!ignore) applySlotsResult(result, null);
      })
      .catch((err) => {
        if (!ignore) applySlotsResult(null, err);
      });
    return () => {
      ignore = true;
    };
  }, [resourceId, applySlotsResult]);

  const loadSlots = useCallback(async () => {
    try {
      applySlotsResult(await getSlots(resourceId), null);
    } catch (err) {
      applySlotsResult(null, err);
    }
  }, [resourceId, applySlotsResult]);

  // Handled the instant a message arrives, straight from useRealtimeConnection's own
  // WebSocket event listener — not via a useEffect reacting to a changed `lastMessage`
  // state, which would set state synchronously inside an effect body.
  const handleRealtimeMessage = useCallback((raw: unknown) => {
    if (raw == null || typeof raw !== 'object') return;
    const message = raw as { type?: string; slot?: SlotInfo; blocked?: string[] };
    if (message.type === 'slot.update' && message.slot) {
      const updated = message.slot;
      setSlots((prev) =>
        prev ? prev.map((s) => (s.id === updated.id ? { ...s, ...updated } : s)) : prev
      );
    } else if (message.type === 'block_result') {
      setBlockingId(null);
      setActionMessage(
        (message.blocked?.length ?? 0) > 0 ? 'Slot blocked.' : 'Slot could not be blocked.'
      );
    }
  }, []);

  const { status, send } = useRealtimeConnection(
    `/ws/resources/${resourceId}/availability`,
    handleRealtimeMessage
  );

  async function handleBlock(slotId: string) {
    setBlockingId(slotId);
    setActionMessage(null);
    const sentOverSocket = status === 'open' && send({ type: 'block_slot', slot_id: slotId });
    if (sentOverSocket) return; // block_result / slot.update will arrive over the socket
    try {
      const result = await blockSlots(resourceId, [slotId]);
      setActionMessage(result.blocked.length > 0 ? 'Slot blocked.' : 'Slot could not be blocked.');
      await loadSlots();
    } catch (err) {
      setActionMessage(err instanceof ApiError ? err.message : 'Could not block this slot');
    } finally {
      setBlockingId(null);
    }
  }

  if (loadError) return <ErrorState description={loadError} onRetry={loadSlots} />;
  if (slots === null) return <LoadingState label="Loading availability…" />;

  return (
    <div className="mx-auto w-full max-w-2xl px-4 py-8">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-neutral-900">Availability</h1>
          {resource && (
            <p className="text-sm text-neutral-600">
              {resource.average_rating ? (
                <>
                  <span className="text-amber-600">★ {resource.average_rating}</span>
                  {' '}
                  ({resource.rating_count} {resource.rating_count === 1 ? 'rating' : 'ratings'})
                </>
              ) : (
                'No ratings yet'
              )}
            </p>
          )}
        </div>
        <span className="flex items-center gap-2 text-xs text-neutral-600">
          <span
            className={`h-2 w-2 rounded-full ${status === 'open' ? 'bg-green-500' : 'bg-neutral-400'}`}
            aria-hidden="true"
          />
          <span aria-live="polite">{status === 'open' ? 'Live' : 'Connecting…'}</span>
        </span>
      </div>
      {actionMessage && (
        <p role="status" aria-live="polite" className="mb-3 text-sm text-neutral-700">
          {actionMessage}
        </p>
      )}
      {slots.length === 0 ? (
        <p className="text-sm text-neutral-600">No slots generated for this resource yet.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-neutral-200">
          {slots.map((slot) => (
            <li key={slot.id} className="flex items-center justify-between gap-3 py-3">
              <span className="text-sm text-neutral-900">{formatSlotTime(slot.start_time)}</span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE_CLASS[slot.status]}`}
              >
                {STATUS_LABEL[slot.status]}
              </span>
              {slot.status === 'AVAILABLE' && (
                <button
                  type="button"
                  onClick={() => handleBlock(slot.id)}
                  disabled={blockingId === slot.id}
                  className="rounded-md border border-neutral-300 px-3 py-1 text-xs text-neutral-700 disabled:opacity-50"
                >
                  {blockingId === slot.id ? 'Blocking…' : 'Block'}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function ResourceAvailabilityPage() {
  const params = useParams<{ resourceId: string }>();
  const resourceId = params.resourceId;

  const [account, setAccount] = useState<BusinessAccount | null | 'loading'>('loading');

  useEffect(() => {
    let cancelled = false;
    me().then((result) => {
      if (!cancelled) setAccount(result);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (account === 'loading') return <LoadingState label="Checking session…" />;
  if (account === null) return <LoginForm onLoggedIn={setAccount} />;
  return <AvailabilityDashboard resourceId={resourceId} />;
}
