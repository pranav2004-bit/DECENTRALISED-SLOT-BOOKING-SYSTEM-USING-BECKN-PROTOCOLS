'use client';

import { useRealtimeConnection } from '@/lib/realtime/useRealtimeConnection';

const STATUS_LABEL = {
  connecting: 'Connecting…',
  open: 'Live',
  closed: 'Disconnected',
  error: 'Connection error',
} as const;

const STATUS_DOT_CLASS = {
  connecting: 'bg-amber-500',
  open: 'bg-green-500',
  closed: 'bg-neutral-400',
  error: 'bg-red-500',
} as const;

export function RealtimeStatus() {
  const { status, reconnect } = useRealtimeConnection();
  const isDown = status === 'closed' || status === 'error';

  return (
    <div className="flex items-center gap-2 text-xs text-neutral-600">
      <span className={`h-2 w-2 rounded-full ${STATUS_DOT_CLASS[status]}`} aria-hidden="true" />
      <span aria-live="polite">{STATUS_LABEL[status]}</span>
      {isDown && (
        <button
          type="button"
          onClick={reconnect}
          className="rounded border border-neutral-300 px-2 py-0.5 text-xs text-neutral-700 focus:outline-none focus:ring-2 focus:ring-neutral-900"
        >
          Retry
        </button>
      )}
    </div>
  );
}
