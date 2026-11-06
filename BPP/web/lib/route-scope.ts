/** Single source of truth for "is this pathname inside the signed-in app interior"
 * (dashboard/staff/orders/profile/etc.) vs. the public landing page or the login/signup
 * forms — used by both AppShell (how to render the header) and AccountProvider
 * (whether there's an account worth fetching at all), so the two can never disagree
 * about which pages the account applies to. */
export function isAppInteriorPath(pathname: string): boolean {
  const isLanding = pathname === '/';
  const isAuthPage = pathname === '/login' || pathname === '/signup';
  return !isLanding && !isAuthPage;
}
