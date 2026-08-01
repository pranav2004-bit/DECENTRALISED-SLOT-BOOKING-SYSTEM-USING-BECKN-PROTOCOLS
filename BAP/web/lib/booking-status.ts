import type { Order } from './beckn-types';

/**
 * livetracker3.md §9.1's fifth/sixth self-audits: the "My Bookings" list needs a
 * per-row status without N simultaneous live `/status` polls (the detail page's own
 * mechanism, `app/bookings/[transactionId]/page.tsx`, which is poll-based and not
 * reusable here). A real cancellation is a one-way, terminal Beckn action — no
 * "un-cancel" — so `cancelled_order`'s mere presence in the bulk list response is
 * already a reliable, static signal on its own. Checked in this exact priority
 * order: cancelled_order wins over updated_order, since a booking can genuinely be
 * both rescheduled and later cancelled.
 *
 * Known limitation (§9.1's sixth self-audit, not fixed here — see
 * `livetracker3.md` Phase 10): `trigger_cancel()` resets `cancelled_order` to null
 * at the start of every cancel attempt, so a failed re-cancel of an already-
 * cancelled booking could transiently wipe this signal. Phase 10 closes that gap
 * at the source; this derivation trusts the field as the backend guarantees it.
 */
export type BookingListStatus = 'CANCELLED' | 'RESCHEDULED' | 'ACTIVE' | 'COMPLETE';

export const BOOKING_LIST_STATUS_LABEL: Record<BookingListStatus, string> = {
  CANCELLED: 'Cancelled',
  RESCHEDULED: 'Rescheduled',
  ACTIVE: 'Confirmed',
  COMPLETE: 'Completed',
};

export function deriveBookingListStatus(booking: {
  confirmed_order: Order | null;
  cancelled_order: Order | null;
  updated_order: Order | null;
}): BookingListStatus {
  if (booking.cancelled_order) return 'CANCELLED';
  if (booking.updated_order) return 'RESCHEDULED';
  return booking.confirmed_order?.status === 'COMPLETE' ? 'COMPLETE' : 'ACTIVE';
}
