import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush } = vi.hoisted(() => ({ mockPush: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mockPush }) }));

import OrdersPage from './page';
import * as authApi from '@/lib/auth-api';
import * as resourcesApi from '@/lib/resources-api';
import * as ordersApi from '@/lib/orders-api';

// Mirrors the page's own formatFullDate() exactly — asserting against this
// rather than a hardcoded literal keeps the test independent of the test
// runner's locale (day/month ordering varies: "June 30, 2025" vs "30 June 2025").
function formatFullDate(date: Date): string {
  return date.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  });
}

const OWNER: authApi.BusinessAccount = {
  id: 'owner-1',
  business_name: 'Glow Salon',
  contact: 'owner@example.com',
  domain_code: 'ONDC:RET13',
  role: 'OWNER',
  managed_by: null,
  is_accepting_bookings: true,
  description: '',
  public_phone: '',
  public_email: '',
  address: '',
  state: '',
  division: '',
  pincode: '',
  // Joining year is well before "today" below, so the history year list has
  // more than just the current year to verify against.
  created_at: '2024-03-10T00:00:00Z',
};

const STAFF: authApi.BusinessAccount = {
  ...OWNER,
  id: 'staff-1',
  business_name: 'Stylist One',
  contact: 'staff@example.com',
  role: 'STAFF',
  managed_by: 'owner-1',
  assigned_resource_ids: [],
};

describe('OrdersPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    // Faking only Date (not setTimeout/setInterval) — testing-library's own
    // waitFor polling needs real timers to ever resolve.
    vi.useFakeTimers({ toFake: ['Date'] });
    // Thu 15 Oct 2026 — a mid-month weekday so "today" and the strip's own
    // bounds are both unambiguous regardless of where the test runs.
    vi.setSystemTime(new Date(2026, 9, 15, 12, 0, 0));
    // Default: no doctors — individual tests override this when the doctor
    // filter's contents matter. Prevents the real fetchOwnedResources from
    // ever reaching apiFetch (unconfigured in this test environment).
    vi.spyOn(resourcesApi, 'fetchOwnedResources').mockResolvedValue([]);
    // Default: no orders — individual tests override this when the order
    // list's contents matter. Same reason as fetchOwnedResources above:
    // prevents the real fetchOrders from ever reaching apiFetch.
    vi.spyOn(ordersApi, 'fetchOrders').mockResolvedValue({ orders: [], next_cursor: null });
  });

  afterEach(() => {
    vi.useRealTimers();
    // Found live on a real CI run (2026-11-08): restoreAllMocks() tears fetchOrders/
    // fetchOwnedResources back down to their real implementations immediately, but a
    // still-in-flight effect from the test that just finished can fire its own
    // (now-unmocked) call in the gap before the next test's beforeEach re-mocks them —
    // hitting the real apiFetch (NEXT_PUBLIC_API_BASE_URL unconfigured here) and
    // throwing an unhandled rejection blamed on whatever test happens to be running
    // next. clearAllMocks() keeps every mock's current implementation in place (so
    // that stale call still resolves harmlessly) while still resetting call history —
    // safe here because every test's own beforeEach/body unconditionally re-mocks
    // whatever it needs anyway, nothing relies on an implementation being removed.
    vi.clearAllMocks();
  });

  it('redirects to /login when no session exists', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<OrdersPage />);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/login'));
  });

  it('does not redirect a STAFF account away — orders is reachable for both roles', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(STAFF);
    render(<OrdersPage />);

    expect(await screen.findByText('October 2026')).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('shows the current month/year heading, a History button, and the full date strip', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    expect(await screen.findByText('October 2026')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument();
    // October has 31 days.
    expect(screen.getByRole('button', { name: 'Thu 15' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^\w+ 1$/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^\w+ 31$/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^\w+ 32$/ })).not.toBeInTheDocument();
  });

  it('defaults the selected date to today, and switches it when another date is picked', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    expect(screen.getByRole('button', { name: 'Thu 15' })).toHaveAttribute('aria-pressed', 'true');

    await user.click(screen.getByRole('button', { name: 'Fri 2' }));
    expect(screen.getByRole('button', { name: 'Fri 2' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Thu 15' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('opens History to a year picker bounded by the account’s own join year', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    await user.click(screen.getByRole('button', { name: 'History' }));

    // Joined 2024-03-10, "today" is 2026 — all three years should be offered.
    expect(screen.getByRole('button', { name: '2026' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '2025' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '2024' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '2023' })).not.toBeInTheDocument();
  });

  it('drills year -> month -> date, and picking a date returns to the calendar view with that date selected', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    await user.click(screen.getByRole('button', { name: 'History' }));
    await user.click(screen.getByRole('button', { name: '2025' }));

    expect(screen.getByText('2025')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Jan' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dec' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Jun' }));
    expect(screen.getByText('June 2025')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: formatFullDate(new Date(2025, 5, 30)) })
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: formatFullDate(new Date(2025, 5, 18)) }));

    // Back on the calendar view, now showing June 2025 itself (not still
    // headed "October 2026") — picking a date from History switches the
    // strip to that date's own month, with it selected there too (the strip
    // uses the short "Wed 18" aria-label, not History's full-date one).
    expect(screen.getByText('June 2025')).toBeInTheDocument();
    expect(screen.queryByText('October 2026')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Wed 18' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();

    // "Today" jumps back to the real current month.
    await user.click(screen.getByRole('button', { name: 'Today' }));
    expect(screen.getByText('October 2026')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thu 15' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('button', { name: 'Today' })).not.toBeInTheDocument();
  });

  it('a join year partway through the year starts the month list at the join month, not January', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER); // joined March 2024
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    await user.click(screen.getByRole('button', { name: 'History' }));
    await user.click(screen.getByRole('button', { name: '2024' }));

    expect(screen.getByRole('button', { name: 'Mar' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dec' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Feb' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Jan' })).not.toBeInTheDocument();
  });

  it('the forward window stays at two months ahead, crossing into next year in December', async () => {
    const user = userEvent.setup();
    vi.setSystemTime(new Date(2026, 11, 15, 12, 0, 0)); // Dec 15 2026
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('December 2026');
    await user.click(screen.getByRole('button', { name: 'History' }));

    // Two months ahead of Dec is Feb — the next year must already be reachable.
    expect(screen.getByRole('button', { name: '2027' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '2027' }));
    expect(screen.getByRole('button', { name: 'Jan' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Feb' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mar' })).not.toBeInTheDocument();
  });

  it('a month more than two months out is not yet reachable', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER); // system time: Oct 15 2026
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    await user.click(screen.getByRole('button', { name: 'History' }));

    expect(screen.queryByRole('button', { name: '2027' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '2026' }));
    expect(screen.getByRole('button', { name: 'Dec' })).toBeInTheDocument();
  });

  it('Back steps up one level at a time and finally returns to the calendar view', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    await user.click(screen.getByRole('button', { name: 'History' }));
    await user.click(screen.getByRole('button', { name: '2025' }));
    await user.click(screen.getByRole('button', { name: 'Jun' }));
    expect(screen.getByText('June 2025')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByText('2025')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Jun' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('button', { name: '2025' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Back' })).not.toBeInTheDocument();
  });

  it('shows the bookings table with every required column, empty for now', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    for (const column of [
      'Customer ID', 'Booking ID', 'Customer name', 'Email', 'Phone',
      'Consulted stylist', 'Slot time', 'Amount (INR)', 'Payment status', 'Status',
    ]) {
      expect(screen.getByRole('columnheader', { name: column })).toBeInTheDocument();
    }
    expect(screen.getByText('No bookings for this date.')).toBeInTheDocument();
  });

  it('the doctor filter is populated from the business’s real, current resource list', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue({ ...OWNER, owned_resource_ids: ['doc-1', 'doc-2'] });
    vi.spyOn(resourcesApi, 'fetchOwnedResources').mockResolvedValue([
      {
        id: 'doc-1',
        name: 'Dr. Kavya',
        average_rating: null,
        rating_count: 0,
        assigned_staff_id: null,
        domain_data: {},
        is_active: true,
        price_currency: 'INR',
        price_value: '500.00',
        created_at: '2026-01-01T00:00:00Z',
      },
      {
        id: 'doc-2',
        name: 'Dr. Laxmisree',
        average_rating: null,
        rating_count: 0,
        assigned_staff_id: null,
        domain_data: {},
        is_active: true,
        price_currency: 'INR',
        price_value: '600.00',
        created_at: '2026-01-01T00:00:00Z',
      },
    ]);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    const doctorFilter = await screen.findByLabelText('Filter by consulted stylist');
    expect(resourcesApi.fetchOwnedResources).toHaveBeenCalledWith(['doc-1', 'doc-2']);
    expect(within(doctorFilter).getByRole('option', { name: 'Dr. Kavya' })).toBeInTheDocument();
    expect(within(doctorFilter).getByRole('option', { name: 'Dr. Laxmisree' })).toBeInTheDocument();
  });

  it('search, status filter and doctor filter controls are present and operable', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    const search = screen.getByPlaceholderText('Customer ID or Booking ID');
    await user.type(search, 'CUST-001');
    expect(search).toHaveValue('CUST-001');

    const statusFilter = screen.getByLabelText('Filter by status');
    await user.selectOptions(statusFilter, 'Completed');
    expect(statusFilter).toHaveValue('COMPLETED');
  });

  it('"Clear filters" only appears once a filter is active, and resets all of them', async () => {
    const user = userEvent.setup();
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    render(<OrdersPage />);

    await screen.findByText('October 2026');
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument();

    const search = screen.getByPlaceholderText('Customer ID or Booking ID');
    await user.type(search, 'CUST-001');
    const statusFilter = screen.getByLabelText('Filter by status');
    await user.selectOptions(statusFilter, 'Completed');

    const clearButton = await screen.findByRole('button', { name: 'Clear filters' });
    await user.click(clearButton);

    expect(search).toHaveValue('');
    expect(statusFilter).toHaveValue('ALL');
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument();
  });
});
