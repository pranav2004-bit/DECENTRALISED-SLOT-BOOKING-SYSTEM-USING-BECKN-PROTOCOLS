'use client';

/** Header badge for the Profile page's Status toggle (is_accepting_bookings) —
 * mirrors RealtimeStatus's own dot+label shape and red "live" dot color for visual
 * consistency, but is a genuinely separate signal (business visibility in search, not
 * WebSocket connectivity). Reads straight from AccountProvider via the account prop
 * AppShell already has, so it updates the instant Profile's toggle saves — no extra
 * fetch of its own. */
export function AvailabilityStatus({ isAcceptingBookings }: { isAcceptingBookings: boolean }) {
  return (
    <div className="flex items-center gap-2 text-xs font-medium text-white">
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full ring-1 ring-white/50 ${
          isAcceptingBookings ? 'bg-red-500' : 'bg-neutral-400'
        }`}
      />
      <span aria-live="polite">{isAcceptingBookings ? 'Available' : 'Unavailable'}</span>
    </div>
  );
}
