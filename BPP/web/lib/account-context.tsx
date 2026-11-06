'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { me, type BusinessAccount } from './auth-api';
import { isAppInteriorPath } from './route-scope';

interface AccountContextValue {
  account: BusinessAccount | null;
  loading: boolean;
  /** Any component that mutates the account (Profile page's save, login/signup,
   * logout) calls this so every other consumer — the header title in AppShell being
   * the one that matters most — re-renders with the new value immediately, instead
   * of staying stale until the next full `me()` fetch. */
  setAccount: (account: BusinessAccount | null) => void;
}

const AccountContext = createContext<AccountContextValue | null>(null);

/** One shared `/api/v1/auth/me` fetch + account value for the whole signed-in app
 * interior, instead of AppShell's header and each page independently fetching (and
 * therefore independently caching) their own copy — the exact split that let the
 * header go stale after a Profile page save. Re-fetches on every pathname change
 * while inside the app interior, same trigger AppShell's own effect used before this
 * refactor. */
export function AccountProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [account, setAccount] = useState<BusinessAccount | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isAppInteriorPath(pathname)) {
      setLoading(false);
      return;
    }
    setLoading(true);
    let cancelled = false;
    me().then((result) => {
      if (cancelled) return;
      setAccount(result);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  return (
    <AccountContext.Provider value={{ account, loading, setAccount }}>
      {children}
    </AccountContext.Provider>
  );
}

export function useAccount(): AccountContextValue {
  const ctx = useContext(AccountContext);
  if (!ctx) throw new Error('useAccount must be used within an AccountProvider');
  return ctx;
}
