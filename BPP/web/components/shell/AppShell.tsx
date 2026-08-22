'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { RealtimeStatus } from './RealtimeStatus';
import { AccountStatus } from './AccountStatus';
import { BRAND } from '@/lib/brand';

export function AppShell({ appName, children }: { appName: string; children: ReactNode }) {
  const pathname = usePathname();
  const isLanding = pathname === '/';

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-neutral-900 focus:px-4 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to main content
      </a>
      <header className="sticky top-0 z-40 border-b border-neutral-200 bg-white">
        <div className="flex items-center py-3 pl-10 pr-6 sm:pl-14 sm:pr-8 lg:pl-20 lg:pr-12">
          <Link
            href="/"
            className="flex items-center gap-2 rounded focus:outline-none focus:ring-2 focus:ring-neutral-900"
          >
            <span
              aria-hidden="true"
              className="flex h-8 items-center rounded-md bg-[var(--brand-600)] px-2 text-xs font-extrabold tracking-tight text-white"
            >
              {BRAND.monogram}
            </span>
            <span className="text-sm font-semibold tracking-tight text-neutral-900 sm:text-base">
              {appName}
            </span>
          </Link>
          {isLanding && (
            <nav className="ml-10 hidden items-center gap-6 xl:flex">
              <a
                href="#customers"
                className="text-sm font-medium text-neutral-700 transition-colors hover:text-neutral-900"
              >
                Customers
              </a>
              <a
                href="#features"
                className="text-sm font-medium text-neutral-700 transition-colors hover:text-neutral-900"
              >
                Features
              </a>
              <a
                href="#how-it-works"
                className="text-sm font-medium text-neutral-700 transition-colors hover:text-neutral-900"
              >
                How it works
              </a>
              <a
                href="#accuracy"
                className="text-sm font-medium text-neutral-700 transition-colors hover:text-neutral-900"
              >
                Accuracy
              </a>
              <a
                href="#open-network"
                className="text-sm font-medium text-neutral-700 transition-colors hover:text-neutral-900"
              >
                Open Network
              </a>
            </nav>
          )}
          {isLanding ? (
            <div className="ml-auto flex items-center gap-3">
              <Link
                href="/login"
                className="rounded-md px-4 py-1.5 text-sm font-semibold text-neutral-800 transition-colors hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2"
              >
                Sign into console
              </Link>
              <Link
                href="/signup"
                className="rounded-full bg-neutral-900 px-5 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2"
              >
                List your business
              </Link>
            </div>
          ) : (
            <div className="ml-auto flex items-center gap-4">
              <AccountStatus />
              <RealtimeStatus />
            </div>
          )}
        </div>
      </header>
      <main id="main-content" className="flex flex-1 flex-col">
        {children}
      </main>
      <footer className="border-t border-neutral-200 px-4 py-4 text-center text-xs text-neutral-500 sm:px-6 lg:px-8">
        Beckn Slot Booking
      </footer>
      {isLanding && (
        <button
          type="button"
          aria-label="Chat"
          className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--brand-600)] text-white shadow-lg transition-colors hover:bg-[var(--brand-700)] focus:outline-none focus:ring-2 focus:ring-[var(--brand-600)] focus:ring-offset-2"
        >
          <svg
            viewBox="0 0 24 24"
            className="h-6 w-6"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M4 4h16v12H8l-4 4V4z" />
          </svg>
        </button>
      )}
    </div>
  );
}
