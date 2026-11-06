'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';
import { RealtimeStatus } from './RealtimeStatus';
import { AccountStatus } from './AccountStatus';
import { AvailabilityStatus } from './AvailabilityStatus';
import { TabNav } from './TabNav';
import { useAccount } from '@/lib/account-context';
import { capitalizeWords } from '@/lib/format';
import { isAppInteriorPath } from '@/lib/route-scope';

export function AppShell({ appName, children }: { appName: string; children: ReactNode }) {
  const pathname = usePathname();
  const isLanding = pathname === '/';
  // Login/signup pages show neither the landing nav+CTAs nor the authenticated
  // AccountStatus/RealtimeStatus block — a "Log in" link is redundant on the login
  // page itself (the form already has its own), and a live-order WebSocket status is
  // meaningless before you're authenticated at all.
  const isAuthPage = pathname === '/login' || pathname === '/signup';
  // The signed-in app interior (dashboard/orders/etc.) — distinct from the public
  // landing page and the login/signup forms, both visually (brand-colored header,
  // matching this industry's own color psychology — see lib/brand.ts) and in what
  // identity the header shows (the real signed-in business, not the platform name).
  const isAppInterior = isAppInteriorPath(pathname);

  // Shared with every other consumer (Profile page's save, in particular) via
  // AccountProvider — so editing the business name on /profile updates this header
  // title immediately, in the same render, instead of staying stale until the next
  // full page navigation re-triggered a separate fetch.
  const { account, loading: accountLoading, setAccount } = useAccount();

  const headerTitle =
    isAppInterior && account ? capitalizeWords(account.business_name) : appName;

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-neutral-900 focus:px-4 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to main content
      </a>
      <header
        className={`sticky top-0 z-40 border-b ${
          isAppInterior
            ? 'border-[var(--brand-700)] bg-[var(--brand-600)]'
            : 'border-neutral-200 bg-white'
        }`}
      >
        <div className="relative flex items-center py-3 pl-10 pr-6 sm:pl-14 sm:pr-8 lg:pl-20 lg:pr-12">
          <Link
            href="/"
            className={`flex items-center gap-2 rounded focus:outline-none focus:ring-2 focus:ring-offset-2 ${
              isAppInterior
                ? 'focus:ring-white focus:ring-offset-[var(--brand-600)]'
                : 'focus:ring-neutral-900'
            }`}
          >
            <span
              className={`font-[family-name:var(--font-title)] tracking-tight ${
                isAppInterior
                  ? 'text-lg font-bold text-white sm:text-xl'
                  : 'text-xl font-bold text-neutral-900 sm:text-2xl'
              }`}
            >
              {headerTitle}
            </span>
          </Link>
          {isLanding && (
            <nav className="absolute left-1/2 hidden -translate-x-1/2 items-center gap-6 xl:flex">
              <a
                href="#accuracy"
                className="text-sm font-medium text-neutral-700 transition-colors hover:text-neutral-900"
              >
                Accuracy
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
                href="#open-network"
                className="text-sm font-medium text-neutral-700 transition-colors hover:text-neutral-900"
              >
                Open Network
              </a>
            </nav>
          )}
          {isAppInterior && <TabNav />}
          {isLanding ? (
            <div className="ml-auto flex items-center gap-3">
              <Link
                href="/signup"
                className="rounded-full border border-neutral-300 px-4 py-1.5 text-sm font-semibold text-neutral-800 transition-colors hover:border-neutral-400 hover:bg-neutral-50 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2"
              >
                Sign into console
              </Link>
              <Link
                href="/signup"
                className="hidden rounded-full bg-neutral-900 px-5 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-900 focus:ring-offset-2 sm:inline-block"
              >
                List your business
              </Link>
            </div>
          ) : !isAuthPage ? (
            <div className="ml-auto flex items-center gap-4">
              <AccountStatus
                account={account}
                loading={accountLoading}
                onLoggedOut={() => setAccount(null)}
              />
              <RealtimeStatus />
              {account && <AvailabilityStatus isAcceptingBookings={account.is_accepting_bookings} />}
            </div>
          ) : null}
        </div>
      </header>
      <main
        id="main-content"
        className={`flex flex-1 flex-col ${isAppInterior ? 'bg-neutral-50' : ''}`}
      >
        {children}
      </main>
      {isLanding && (
        <button
          type="button"
          aria-label="Chat"
          className="fixed bottom-6 right-6 z-50 flex h-14 w-14 items-center justify-center rounded-full bg-[var(--brand-600)] text-white shadow-xl shadow-[var(--brand-600)]/40 transition-all hover:-translate-y-0.5 hover:bg-[var(--brand-700)] hover:shadow-2xl hover:shadow-[var(--brand-600)]/50 focus:outline-none focus:ring-2 focus:ring-[var(--brand-600)] focus:ring-offset-2"
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
