'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BRAND } from '@/lib/brand';
import { RESOURCE_TYPES_BY_DOMAIN } from '@/lib/constants';

/** The Resources tab's label — brand-aware, matching signup's own categoryLabel
 * precedent (BPP/web/app/signup/page.tsx): a domain with exactly one resource type
 * (Healthcare -> Doctor, Automotive -> Bay) shows that type's own name, since it's
 * more specific than the generic label ("Doctors"/"Bays" reads better than
 * "Resources" on CareNest/AutoCare); only Beauty still has multiple resource types
 * (Stylist/Chair) with no single equivalent, so it falls back to the generic
 * "Resources". The route stays /resources for every brand regardless — only the
 * displayed label changes, the same label-vs-route split the Bookings tab
 * (-> /orders) already established. */
function resourcesTabLabel(): string {
  const types = RESOURCE_TYPES_BY_DOMAIN[BRAND.domainCode] ?? [];
  return types.length === 1 ? `${types[0].label}s` : 'Resources';
}

const TABS = [
  { label: 'Dashboard', href: '/dashboard' },
  { label: resourcesTabLabel(), href: '/resources' },
  // Staff tab hidden temporarily (2026-10-20, at the owner's own request) — the
  // /staff page and its route are untouched, just not linked from nav for now.
  // { label: 'Staff', href: '/staff' },
  // "Bookings" is the customer-facing name for what the codebase already built as
  // the real-time confirmed-orders list at /orders — not a new/placeholder page.
  { label: 'Bookings', href: '/orders' },
  { label: 'Profile', href: '/profile' },
] as const;

/** Primary in-console navigation, centered in the brand-colored header — a single
 * segmented-control track (one bounded, translucent-white bar) rather than
 * individually floating elements, with the active tab as a solid white segment
 * "raised" inside it. Reads as one deliberate, designed control instead of loose
 * nav items, matching the segmented controls used across macOS/iOS and products
 * like Linear. */
export function TabNav() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Primary"
      className="absolute left-1/2 hidden -translate-x-1/2 items-center gap-0.5 rounded-full bg-white/15 p-1 md:flex"
    >
      {TABS.map((tab) => {
        const isActive = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={isActive ? 'page' : undefined}
            className={`rounded-full px-4 py-1.5 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-white ${
              isActive
                ? 'bg-white font-semibold text-[var(--brand-700)] shadow-sm'
                : 'font-medium text-white hover:bg-white/10'
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
