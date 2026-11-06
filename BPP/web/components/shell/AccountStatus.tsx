'use client';

import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { logout, type BusinessAccount } from '@/lib/auth-api';
import { useToast } from '@/components/ui/Toast';

interface AccountStatusProps {
  account: BusinessAccount | null;
  loading: boolean;
  onLoggedOut: () => void;
}

/** livetracker3.md §7.1 — mirrors BAP/web's own AccountStatus (ADR-0004).
 * `account`/`loading` are owned by AppShell (which also needs the business name for
 * the header title) and passed down, rather than fetched here too. Styled for the
 * brand-colored header it always renders inside (AppShell only mounts this in the
 * signed-in app interior). Dashboard/Orders links used to live here too — now
 * covered by TabNav's own centered nav, so this is just account identity actions. */
export function AccountStatus({ account, loading, onLoggedOut }: AccountStatusProps) {
  const router = useRouter();
  const { showToast } = useToast();

  if (loading) return null;

  if (!account) {
    return (
      <Link href="/login" className="text-xs font-medium text-white underline underline-offset-2">
        Log in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-3 text-xs font-medium text-white">
      <button
        type="button"
        onClick={async () => {
          await logout();
          onLoggedOut();
          showToast('Logged out successfully');
          router.push('/');
          router.refresh();
        }}
        className="cursor-pointer rounded-full bg-white/15 px-4 py-1.5 text-sm font-semibold text-white transition-colors hover:bg-white/25 focus:outline-none focus-visible:ring-2 focus-visible:ring-white"
      >
        Log out
      </button>
    </div>
  );
}
