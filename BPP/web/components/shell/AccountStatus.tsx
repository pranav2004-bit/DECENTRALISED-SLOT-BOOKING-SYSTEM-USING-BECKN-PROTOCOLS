'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { logout, me, type BusinessAccount } from '@/lib/auth-api';

/** livetracker3.md §7.1 — mirrors BAP/web's own AccountStatus (ADR-0004). */
export function AccountStatus() {
  const router = useRouter();
  const pathname = usePathname();
  const [account, setAccount] = useState<BusinessAccount | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Re-checked on every pathname change, not just on mount — a client-side route
    // change (router.push after login/logout) does not remount AppShell/AccountStatus,
    // matching BAP's own AccountStatus's own established fix for the same issue.
    me().then((a) => {
      setAccount(a);
      setLoading(false);
    });
  }, [pathname]);

  if (loading) return null;

  if (!account) {
    return (
      <Link href="/login" className="text-xs text-neutral-600 underline">
        Log in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-3 text-xs text-neutral-600">
      <Link href="/dashboard" className="underline">
        Dashboard
      </Link>
      {/* livetracker6.md §2.2: reachable for both roles — an owner sees every owned
          resource's orders, staff see only their one assigned resource's own. */}
      <Link href="/orders" className="underline">
        Orders
      </Link>
      <span>{account.business_name}</span>
      <button
        type="button"
        onClick={async () => {
          await logout();
          setAccount(null);
          router.push('/');
          router.refresh();
        }}
        className="rounded border border-neutral-300 px-2 py-0.5 text-xs text-neutral-700 focus:outline-none focus:ring-2 focus:ring-neutral-900"
      >
        Log out
      </button>
    </div>
  );
}
