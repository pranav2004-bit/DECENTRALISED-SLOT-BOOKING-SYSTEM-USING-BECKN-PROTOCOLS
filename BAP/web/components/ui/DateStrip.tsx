'use client';

import { useState } from 'react';

const pad = (n: number) => String(n).padStart(2, '0');

export function dateToIso(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Only today's date needs a time floor (can't book a past time today); every
 * later date in the strip is already guaranteed to be in the future. */
export function minTimeForDate(dateIso: string): string | undefined {
  const now = new Date();
  if (dateIso !== dateToIso(now)) return undefined;
  const floor = new Date(now.getTime() + 5 * 60 * 1000);
  return `${pad(floor.getHours())}:${pad(floor.getMinutes())}`;
}

/** Always derived from `new Date()` at call time, never hardcoded — so "today"
 * (and the 14 days after it) are correct on whatever day this actually runs,
 * no stale array to keep in sync by hand. */
function buildDateOptions(): { iso: string; weekday: string; dayNum: number; month: string; isToday: boolean }[] {
  const now = new Date();
  return Array.from({ length: 15 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
    return {
      iso: dateToIso(d),
      weekday: d.toLocaleDateString('en-IN', { weekday: 'short' }),
      dayNum: d.getDate(),
      month: d.toLocaleDateString('en-IN', { month: 'short' }),
      isToday: i === 0,
    };
  });
}

export function DateStrip({
  value,
  onChange,
  edgeToEdge = false,
}: {
  value: string;
  onChange: (iso: string) => void;
  /** Flush against the viewport's top/left/right instead of sitting as a
   * rounded, fully-bordered card — no rounding, no side borders, just the
   * top/bottom divider lines, for callers that already cancel their
   * container's own padding around this component. */
  edgeToEdge?: boolean;
}) {
  const [days] = useState(buildDateOptions);
  return (
    <div
      className={`flex divide-x divide-neutral-200 overflow-x-auto bg-white [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${
        edgeToEdge ? 'border-y border-neutral-200' : 'rounded-xl border border-neutral-200 shadow-sm'
      }`}
    >
      {days.map((d) => {
        const active = d.iso === value;
        return (
          <button
            key={d.iso}
            type="button"
            onClick={() => onChange(d.iso)}
            aria-pressed={active}
            className={`flex w-14 flex-none flex-col items-center gap-0.5 py-2.5 text-center transition-colors ${
              active ? 'bg-[var(--brand-600)] text-white' : 'bg-white text-neutral-700 hover:bg-neutral-50'
            }`}
          >
            <span
              className={`text-[10px] font-semibold uppercase tracking-wide ${active ? 'text-white' : 'text-neutral-500'}`}
            >
              {d.isToday ? 'Today' : d.weekday}
            </span>
            <span className="text-base font-semibold tabular-nums">{d.dayNum}</span>
            <span
              className={`text-[10px] font-semibold uppercase tracking-wide ${active ? 'text-white' : 'text-neutral-400'}`}
            >
              {d.month}
            </span>
          </button>
        );
      })}
    </div>
  );
}
