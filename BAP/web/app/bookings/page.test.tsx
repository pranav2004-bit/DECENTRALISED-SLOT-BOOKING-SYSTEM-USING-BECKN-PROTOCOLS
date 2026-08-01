import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockPush } = vi.hoisted(() => ({ mockPush: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
}));

import BookingsPage from './page';
import * as authApi from '@/lib/auth-api';
import * as bookingsApi from '@/lib/bookings-api';

const CUSTOMER = { id: 'c-1', name: 'Ada', contact: 'ada@example.com', notify_by_email: true };

function booking(overrides: Partial<bookingsApi.BookingListItem> = {}): bookingsApi.BookingListItem {
  return {
    transaction_id: 'txn-1',
    domain: 'ONDC:RET13',
    confirmed_order: {
      id: 'o-1',
      status: 'ACTIVE',
      provider: { id: 'p-1' },
      items: [{ id: 'i-1' }],
      quote: {
        price: { currency: 'INR', value: '500.00' },
        breakup: [{ item: { id: 'i-1' }, title: 'Haircut', price: { currency: 'INR', value: '500.00' } }],
      },
      fulfillments: [{ id: 'f-1', stops: [{ type: 'start', time: { timestamp: '2026-08-05T10:00:00+00:00' } }] }],
    },
    cancelled_order: null,
    updated_order: null,
    created_at: '2026-08-01T09:00:00+00:00',
    ...overrides,
  };
}

describe('BookingsPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('redirects to /account when no session is present', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    const listSpy = vi.spyOn(bookingsApi, 'listBookings');
    render(<BookingsPage />);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/account'));
    expect(listSpy).not.toHaveBeenCalled();
  });

  it('shows the real empty state for a logged-in customer with zero bookings', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(CUSTOMER);
    vi.spyOn(bookingsApi, 'listBookings').mockResolvedValue({ bookings: [], next_cursor: null });
    render(<BookingsPage />);

    expect(await screen.findByText('No bookings yet')).toBeInTheDocument();
  });

  it('lists a logged-in customer’s bookings, newest first, with domain and status, linking to the detail page', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(CUSTOMER);
    vi.spyOn(bookingsApi, 'listBookings').mockResolvedValue({
      bookings: [
        booking({ transaction_id: 'txn-active', domain: 'ONDC:RET13' }),
        booking({
          transaction_id: 'txn-cancelled',
          domain: 'ONDC:SRV13',
          cancelled_order: {
            id: 'o-2',
            status: 'CANCELLED',
            provider: { id: 'p-1' },
            items: [{ id: 'i-1' }],
            fulfillments: [],
          },
          confirmed_order: {
            id: 'o-2',
            status: 'ACTIVE',
            provider: { id: 'p-1' },
            items: [{ id: 'i-1' }],
            quote: {
              price: { currency: 'INR', value: '900.00' },
              breakup: [
                { item: { id: 'i-1' }, title: 'Dental Checkup', price: { currency: 'INR', value: '900.00' } },
              ],
            },
            fulfillments: [
              { id: 'f-2', stops: [{ type: 'start', time: { timestamp: '2026-08-06T10:00:00+00:00' } }] },
            ],
          },
        }),
      ],
      next_cursor: null,
    });
    render(<BookingsPage />);

    expect(await screen.findByText('Haircut')).toBeInTheDocument();
    expect(screen.getByText('Dental Checkup')).toBeInTheDocument();
    expect(screen.getByText('Beauty & Wellness')).toBeInTheDocument();
    expect(screen.getByText('Healthcare')).toBeInTheDocument();
    expect(screen.getByText('Confirmed')).toBeInTheDocument();
    expect(screen.getByText('Cancelled')).toBeInTheDocument();

    expect(screen.getByRole('link', { name: /Haircut/ })).toHaveAttribute(
      'href',
      '/bookings/txn-active'
    );
  });

  it('loads the next page via cursor pagination and appends results', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(CUSTOMER);
    vi.spyOn(bookingsApi, 'listBookings')
      .mockResolvedValueOnce({
        bookings: [booking({ transaction_id: 'txn-1' })],
        next_cursor: '42',
      })
      .mockResolvedValueOnce({
        bookings: [booking({ transaction_id: 'txn-0' })],
        next_cursor: null,
      });
    render(<BookingsPage />);

    await screen.findByRole('link', { name: /Haircut/ });
    const loadMore = screen.getByRole('button', { name: 'Load more' });
    await userEvent.click(loadMore);

    await waitFor(() => expect(bookingsApi.listBookings).toHaveBeenCalledWith('42'));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
    );
    expect(screen.getAllByRole('link', { name: /Haircut/ })).toHaveLength(2);
  });
});
