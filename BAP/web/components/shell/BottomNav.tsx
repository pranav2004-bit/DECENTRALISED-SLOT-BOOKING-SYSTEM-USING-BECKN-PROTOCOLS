'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

/**
 * Primary mobile navigation — the pattern every reference app named for this
 * redesign (BookMyShow/PhonePe/Paytm/Ola/Rapido) actually uses: a persistent
 * bottom tab bar on small screens, nothing extra on larger ones where the
 * existing top bar already does the job. No dedicated Search tab — those same
 * reference apps don't give search its own bottom-nav slot either; browsing
 * happens from Home's category grid, so the bar stays destinations-only.
 */

const TABS = [
  { href: '/', label: 'Home', icon: HomeIcon },
  { href: '/bookings', label: 'Bookings', icon: BookingsIcon },
  { href: '/account', label: 'Account', icon: AccountIcon },
] as const;

function HomeIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 10.5 12 3l9 7.5" />
      <path d="M5 9.5V21h14V9.5" />
    </svg>
  );
}

function BookingsIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </svg>
  );
}

function AccountIcon({ active }: { active: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={active ? 2.5 : 2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c1.6-3.6 5-5.5 8-5.5s6.4 1.9 8 5.5" />
    </svg>
  );
}

export function BottomNav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Primary"
      className="fixed inset-x-0 bottom-0 z-40 border-t border-neutral-200 bg-white/95 pb-[env(safe-area-inset-bottom)] shadow-[0_-1px_8px_rgba(0,0,0,0.06)] backdrop-blur sm:hidden"
    >
      <div className="mx-auto flex max-w-md items-stretch justify-around">
        {TABS.map(({ href, label, icon: Icon }) => {
          const active = href === '/' ? pathname === '/' : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className="flex flex-1 flex-col items-center gap-0.5 py-2.5 text-[11px] font-medium focus:outline-none"
            >
              <span className={active ? 'text-[var(--brand-600)]' : 'text-neutral-400'}>
                <Icon active={active} />
              </span>
              <span className={active ? 'font-semibold text-[var(--brand-600)]' : 'text-neutral-500'}>
                {label}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
