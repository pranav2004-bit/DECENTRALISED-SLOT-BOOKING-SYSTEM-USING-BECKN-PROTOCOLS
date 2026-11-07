import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

/** Stat-card values are plain, small numbers — easily ambiguous against the
 * trend chart's own per-day counts/dates on the same page. Scopes the lookup
 * to the one card with this exact label instead. */
function getStatValue(label: string): string {
  const labelEl = screen.getByText(label);
  const card = labelEl.closest('div');
  // The value lives inside a wrapper span (value + optional change badge) —
  // its own first child is the plain value text, the badge (if any) is the second.
  const valueEl = card?.querySelector('span:nth-child(2) > span:first-child');
  if (!valueEl) throw new Error(`No value found for stat card labeled "${label}"`);
  return valueEl.textContent ?? '';
}

const { mockPush } = vi.hoisted(() => ({ mockPush: vi.fn() }));
vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mockPush }) }));

import DashboardPage from './page';
import * as authApi from '@/lib/auth-api';
import * as dashboardApi from '@/lib/dashboard-api';

const OWNER: authApi.BusinessAccount = {
  id: 'owner-1',
  business_name: 'Pavan Group Of Hospitals',
  contact: 'owner@example.com',
  domain_code: 'ONDC:SRV13',
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
  created_at: '2024-03-10T00:00:00Z',
};

const STATS: dashboardApi.DashboardStats = {
  total_doctors: 5,
  doctors_available_today: 3,
  doctors_unavailable_today: 2,
  overall_rating: '4.6',
  rating_count: 128,
  earnings_today: '12500.00',
  earnings_today_change_pct: 12.5,
  earnings_this_month: '340000.00',
  earnings_this_month_change_pct: -4.2,
  total_bookings: 812,
  bookings_today: 9,
  bookings_today_change_pct: null,
  pending_payment_count: 3,
  avg_booking_value: '620.00',
  cancellation_rate: '8.3',
  top_doctor: { name: 'Dr. Kavya', revenue: '45000.00', bookings: 20 },
  doctor_performance: [
    { name: 'Dr. Kavya', specialisation: 'Gynecology & Obstetrics', fee: '500.00', bookings: 20, revenue: '45000.00' },
    { name: 'Dr. Mehta', specialisation: '', fee: '400.00', bookings: 0, revenue: '0.00' },
  ],
  currency: 'INR',
  trend: [
    { date: '2026-11-01', bookings: 4, revenue: '2000.00' },
    { date: '2026-11-02', bookings: 6, revenue: '3000.00' },
    { date: '2026-11-03', bookings: 0, revenue: '0.00' },
    { date: '2026-11-04', bookings: 5, revenue: '2500.00' },
    { date: '2026-11-05', bookings: 8, revenue: '4000.00' },
    { date: '2026-11-06', bookings: 2, revenue: '1000.00' },
    { date: '2026-11-07', bookings: 9, revenue: '12500.00' },
  ],
};

describe('DashboardPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('redirects to /login when no session exists', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<DashboardPage />);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/login'));
  });

  it('shows the real stat figures once loaded, scoped per the logged-in account', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(dashboardApi, 'fetchDashboardStats').mockResolvedValue(STATS);
    render(<DashboardPage />);

    await screen.findByText('Welcome back');
    expect(getStatValue('Total stylists')).toBe('5');
    expect(getStatValue('Available today')).toBe('3');
    expect(getStatValue('Not available today')).toBe('2');
    expect(getStatValue('Overall rating')).toBe('4.6 ★');
    expect(getStatValue('Pending payments')).toBe('3');
    expect(getStatValue('Cancellation rate')).toBe('8.3%');
    expect(getStatValue('Avg. booking value')).toBe('₹620');
    expect(getStatValue('Top stylist (this month)')).toBe('Dr. Kavya - ₹45,000');
    expect(screen.getByText('Dr. Kavya')).toBeInTheDocument();
    expect(screen.getByText('Gynecology & Obstetrics')).toBeInTheDocument();
    expect(screen.getByText('Dr. Mehta')).toBeInTheDocument();
    expect(getStatValue('Total bookings')).toBe('812');
    expect(getStatValue('Bookings today')).toBe('9');
    // Whole-rupee Indian-locale formatting (no decimals) — matches
    // BAP/web's own formatPrice convention.
    expect(getStatValue("Today's earnings")).toBe('₹12,500');
    expect(getStatValue("This month's earnings")).toBe('₹3,40,000');
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('shows an error state if the stats request fails, without crashing the page', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(OWNER);
    vi.spyOn(dashboardApi, 'fetchDashboardStats').mockRejectedValue(new Error('network error'));
    render(<DashboardPage />);

    expect(await screen.findByText("Couldn't load the dashboard")).toBeInTheDocument();
  });
});
