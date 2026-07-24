'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { logout, me, type Customer } from '@/lib/auth-api';

export function AccountStatus() {
  const router = useRouter();
  const pathname = usePathname();
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // AppShell/AccountStatus lives in the root layout, which a client-side
    // route change (router.push after login/logout) does not remount — so a
    // mount-only effect would leave the header stuck showing the pre-login
    // state until a hard reload. Re-checking on every pathname change picks
    // up the new session right after the post-login/post-logout redirect.
    me().then((c) => {
      setCustomer(c);
      setLoading(false);
    });
  }, [pathname]);

  if (loading) return null;

  if (!customer) {
    return (
      <Link href="/account" className="text-xs text-neutral-600 underline">
        Log in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2 text-xs text-neutral-600">
      <span>Signed in as {customer.name}</span>
      <button
        type="button"
        onClick={async () => {
          await logout();
          setCustomer(null);
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
