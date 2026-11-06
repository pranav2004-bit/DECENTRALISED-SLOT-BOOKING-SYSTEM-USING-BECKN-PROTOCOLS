import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import Home from './page';
import * as authApi from '@/lib/auth-api';
import * as bookingsApi from '@/lib/bookings-api';

const CUSTOMER: authApi.Customer = {
  id: 'c-1',
  name: 'Lohit Reddy',
  contact: 'lohit@example.com',
  notify_by_email: true,
};

function booking(overrides: Partial<bookingsApi.BookingListItem> = {}): bookingsApi.BookingListItem {
  return {
    transaction_id: 'txn-1',
    domain: 'ONDC:SRV13',
    confirmed_order: {
      id: 'o-1',
      status: 'ACTIVE',
      provider: { id: 'p-1' },
      items: [{ id: 'i-1' }],
      quote: {
        price: { currency: 'INR', value: '900.00' },
        breakup: [{ item: { id: 'i-1' }, title: 'Dental Checkup', price: { currency: 'INR', value: '900.00' } }],
      },
      fulfillments: [{ id: 'f-1', stops: [{ type: 'start', time: { timestamp: '2099-01-05T10:00:00+00:00' } }] }],
    },
    cancelled_order: null,
    updated_order: null,
    created_at: '2026-08-01T09:00:00+00:00',
    ...overrides,
  };
}

describe('Home', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('greets an anonymous visitor generically, with no upcoming-booking card', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<Home />);

    expect(await screen.findByText('Welcome to OnSlot')).toBeInTheDocument();
    expect(screen.getByText('What would you like to book today?')).toBeInTheDocument();
    expect(screen.queryByText('Upcoming booking')).not.toBeInTheDocument();
  });

  it('greets a logged-in customer by first name only, not their full name', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(CUSTOMER);
    vi.spyOn(bookingsApi, 'listBookings').mockResolvedValue({ bookings: [], next_cursor: null });
    render(<Home />);

    expect(await screen.findByText('Hi, Lohit')).toBeInTheDocument();
    expect(screen.queryByText('Hi, Lohit Reddy')).not.toBeInTheDocument();
  });

  it('renders exactly the 3 real domains as category tiles, each deep-linking into /search', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(null);
    render(<Home />);

    await screen.findByText('Explore');
    expect(screen.getByRole('link', { name: /Wellness/ })).toHaveAttribute(
      'href',
      '/search?domain=ONDC%3ARET13'
    );
    expect(screen.getByRole('link', { name: /Healthcare/ })).toHaveAttribute(
      'href',
      '/search?domain=ONDC%3ASRV13'
    );
    expect(screen.getByRole('link', { name: /Automotive/ })).toHaveAttribute(
      'href',
      '/search?domain=BECKN%3AAUTO01'
    );
  });

  it('shows a real upcoming booking for a logged-in customer, linking to its detail page', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(CUSTOMER);
    vi.spyOn(bookingsApi, 'listBookings').mockResolvedValue({
      bookings: [booking()],
      next_cursor: null,
    });
    render(<Home />);

    expect(await screen.findByText('Upcoming booking')).toBeInTheDocument();
    expect(screen.getByText('Dental Checkup')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Upcoming booking/ })).toHaveAttribute(
      'href',
      '/bookings/txn-1'
    );
  });

  it('does not show an upcoming-booking card when the only bookings are cancelled or in the past', async () => {
    vi.spyOn(authApi, 'me').mockResolvedValue(CUSTOMER);
    vi.spyOn(bookingsApi, 'listBookings').mockResolvedValue({
      bookings: [
        booking({
          transaction_id: 'txn-cancelled',
          cancelled_order: { id: 'o-2', provider: { id: 'p-1' }, items: [], fulfillments: [] },
        }),
        booking({
          transaction_id: 'txn-past',
          confirmed_order: {
            id: 'o-3',
            status: 'ACTIVE',
            provider: { id: 'p-1' },
            items: [{ id: 'i-1' }],
            quote: {
              price: { currency: 'INR', value: '500.00' },
              breakup: [{ item: { id: 'i-1' }, title: 'Old Haircut', price: { currency: 'INR', value: '500.00' } }],
            },
            fulfillments: [{ id: 'f-3', stops: [{ type: 'start', time: { timestamp: '2020-01-01T10:00:00+00:00' } }] }],
          },
        }),
      ],
      next_cursor: null,
    });
    render(<Home />);

    await screen.findByText('Hi, Lohit');
    await waitFor(() => expect(bookingsApi.listBookings).toHaveBeenCalled());
    expect(screen.queryByText('Upcoming booking')).not.toBeInTheDocument();
  });
});
