import { ErrorState } from './ErrorState';

/**
 * Named, reusable error-state variants for the booking flow (livetracker2.md §3.6).
 * Built on the existing generic ErrorState — not wired into any real page yet, since
 * the real booking-flow screens (search/select/confirm) are §3.9 "Booking Flow UI"'s
 * job, not yet built as of this phase (confirmed by inspecting BAP/web/app: only the
 * Phase 2.4 shell exists). Ready for §3.9 to import once those screens land.
 */

export function BookingFailedError({ onRetry }: { onRetry?: () => void }) {
  return (
    <ErrorState
      title="Booking failed"
      description="We couldn't complete your booking. Please try again."
      onRetry={onRetry}
    />
  );
}

export function SessionExpiredError({ onLogin }: { onLogin?: () => void }) {
  return (
    <ErrorState
      title="Session expired"
      description="Please log in again to continue."
      onRetry={onLogin}
      actionLabel="Log in"
    />
  );
}

export function SlotUnavailableError({ onChooseAnother }: { onChooseAnother?: () => void }) {
  return (
    <ErrorState
      title="Slot no longer available"
      description="This time slot was just booked by someone else. Please choose another time."
      onRetry={onChooseAnother}
      actionLabel="Choose another slot"
    />
  );
}
