'use client';

import Link from 'next/link';
import type { ReactNode } from 'react';
import { RealtimeStatus } from './RealtimeStatus';
import { AccountStatus } from './AccountStatus';
import { BottomNav } from './BottomNav';
import { HeaderContentProvider, useHeaderContent } from './HeaderContext';

function HeaderLeft({ appName }: { appName: string }) {
  const customContent = useHeaderContent();
  if (customContent) return <>{customContent}</>;
  return (
    <Link
      href="/"
      aria-label={appName}
      className="rounded font-[family-name:var(--font-title)] text-lg font-extrabold tracking-tight text-neutral-900 focus:outline-none focus:ring-2 focus:ring-neutral-900 sm:text-xl"
    >
      Ease begins here.
    </Link>
  );
}

export function AppShell({ appName, children }: { appName: string; children: ReactNode }) {
  return (
    <HeaderContentProvider>
      <div className="flex min-h-full flex-1 flex-col">
        <a
          href="#main-content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-neutral-900 focus:px-4 focus:py-2 focus:text-sm focus:text-white"
        >
          Skip to main content
        </a>
        <header className="sticky top-0 z-30 border-b border-neutral-200 bg-white shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
          <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
            <HeaderLeft appName={appName} />
            {/* Mobile has its own Account tab in the bottom nav — this row would
                just duplicate it there. Desktop has no bottom nav, so this stays
                its only way to see login state / reach /account. */}
            <div className="hidden items-center gap-4 sm:flex">
              <AccountStatus />
              <RealtimeStatus />
            </div>
          </div>
        </header>
        <main id="main-content" className="flex flex-1 flex-col pb-16 sm:pb-0">
          {children}
        </main>
        <footer className="hidden border-t border-neutral-200 bg-white px-4 py-4 text-center text-xs text-neutral-500 sm:block sm:px-6 lg:px-8">
          Beckn Slot Booking
        </footer>
        <BottomNav />
      </div>
    </HeaderContentProvider>
  );
}
