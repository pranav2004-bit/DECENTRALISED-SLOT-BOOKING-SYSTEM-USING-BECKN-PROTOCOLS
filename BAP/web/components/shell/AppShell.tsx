import Link from 'next/link';
import type { ReactNode } from 'react';
import { RealtimeStatus } from './RealtimeStatus';

export function AppShell({ appName, children }: { appName: string; children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-1 flex-col">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-neutral-900 focus:px-4 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to main content
      </a>
      <header className="border-b border-neutral-200">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3 sm:px-6 lg:px-8">
          <Link
            href="/"
            className="rounded text-sm font-semibold tracking-tight text-neutral-900 focus:outline-none focus:ring-2 focus:ring-neutral-900 sm:text-base"
          >
            {appName}
          </Link>
          <RealtimeStatus />
        </div>
      </header>
      <main id="main-content" className="flex flex-1 flex-col">
        {children}
      </main>
      <footer className="border-t border-neutral-200 px-4 py-4 text-center text-xs text-neutral-500 sm:px-6 lg:px-8">
        Beckn Slot Booking
      </footer>
    </div>
  );
}
