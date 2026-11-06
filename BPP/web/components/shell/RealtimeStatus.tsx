'use client';

import { useRealtimeConnection } from '@/lib/realtime/useRealtimeConnection';

const STATUS_LABEL = {
  connecting: 'Connecting…',
  open: 'Live',
  closed: 'Disconnected',
  error: 'Connection error',
  forbidden: 'Access denied',
} as const;

const STATUS_DOT_CLASS = {
  connecting: 'bg-amber-500',
  // "Live" reads as a red broadcast-style indicator (a REC/LIVE dot), not the
  // traffic-light "all good" green — matches how YouTube/Twitch-style live badges
  // read, at the request of design feedback on this exact dot.
  open: 'bg-red-500',
  closed: 'bg-neutral-400',
  error: 'bg-red-500',
  forbidden: 'bg-red-500',
} as const;

export function RealtimeStatus() {
  const { status, reconnect } = useRealtimeConnection();
  const isDown = status === 'closed' || status === 'error' || status === 'forbidden';

  return (
    <div className="group relative flex items-center gap-2 text-xs font-medium text-white">
      <span
        className={`h-2 w-2 rounded-full ring-1 ring-white/50 ${STATUS_DOT_CLASS[status]}`}
        aria-hidden="true"
      />
      <span aria-live="polite">{STATUS_LABEL[status]}</span>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-50 mt-2 -translate-x-1/2 whitespace-nowrap rounded-md bg-neutral-900 px-2.5 py-1.5 text-xs font-medium text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100"
      >
        Registry connection
      </span>
      {isDown && (
        <button
          type="button"
          onClick={reconnect}
          className="rounded border border-white/40 px-2 py-0.5 text-xs text-white hover:bg-white/10 focus:outline-none focus:ring-2 focus:ring-white"
        >
          Retry
        </button>
      )}
    </div>
  );
}
