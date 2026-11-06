'use client';

import type { ReactNode } from 'react';

/** A single-word (or short) hover label with a dark popup background — the same
 * pattern already proven on the header's "Live" indicator, extracted here so every
 * icon-only/ambiguous button across the app can reuse it instead of re-deriving the
 * same group-hover markup each time. */
export function Tooltip({
  label,
  children,
  className = '',
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={`group relative inline-flex ${className}`}>
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-50 mt-2 -translate-x-1/2 whitespace-nowrap rounded-md bg-neutral-900 px-2.5 py-1.5 text-xs font-medium text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100"
      >
        {label}
      </span>
    </span>
  );
}
