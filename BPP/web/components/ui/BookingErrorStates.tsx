import { ErrorState } from './ErrorState';

/**
 * Named, reusable error-state variants (livetracker2.md §3.6) — mirrors
 * BAP/web/components/ui/BookingErrorStates.tsx exactly, same as Phase 2.4's own
 * "mirror the same shell + component library to BPP/web" convention. Built on the
 * existing generic ErrorState — not wired into any real page yet, since the real
 * booking-flow screens are §3.9's job, not yet built as of this phase.
 */

export function BookingFailedError({ onRetry }: { onRetry?: () => void }) {
  return (
    <ErrorState
      title="Booking failed"
      description="We couldn't complete this booking. Please try again."
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
