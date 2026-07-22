import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockUseParams, mockUseSearchParams } = vi.hoisted(() => ({
  mockUseParams: vi.fn(),
  mockUseSearchParams: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useParams: mockUseParams,
  useSearchParams: mockUseSearchParams,
}));

import BookingStatusPage from './page';
import * as bookingApi from '@/lib/booking-api';

const CONFIRMED_ORDER = {
  id: 'booking-1',
  status: 'ACTIVE',
  provider: { id: 'provider-1' },
  items: [{ id: 'item-1' }],
  fulfillments: [
    { id: 'booking-1', stops: [{ type: 'start', time: { timestamp: '2026-08-01T10:00:00+00:00' } }] },
  ],
  quote: {
    price: { currency: 'INR', value: '500.00' },
    breakup: [{ item: { id: 'item-1' }, title: 'Haircut', price: { currency: 'INR', value: '500.00' } }],
  },
};

describe('BookingStatusPage', () => {
  beforeEach(() => {
    mockUseParams.mockReturnValue({ transactionId: 'tx-1' });
    mockUseSearchParams.mockReturnValue(new URLSearchParams({ provider_name: 'Glow Salon' }));
    vi.spyOn(bookingApi, 'triggerStatus').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getStatusResult').mockResolvedValue({
      transaction_id: 'tx-1',
      status_order: { ...CONFIRMED_ORDER },
      status_error: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows a loading state, then the confirmed booking details', async () => {
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    render(<BookingStatusPage />);

    expect(screen.getByRole('status')).toHaveTextContent('Loading your booking');
    await waitFor(() => expect(screen.getByText('Haircut')).toBeInTheDocument());
    expect(screen.getByText('Glow Salon')).toBeInTheDocument();
    expect(screen.getByText('₹500.00')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel booking' })).toBeInTheDocument();
  });

  it('shows an empty state when there is no confirmed booking for this reference', async () => {
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_order: null,
      confirmed_error: null,
    });
    render(<BookingStatusPage />);

    expect(await screen.findByText('Booking not found')).toBeInTheDocument();
  });

  it('shows a load error when fetching the booking fails', async () => {
    const { ApiError } = await import('@/lib/api-client');
    vi.spyOn(bookingApi, 'getConfirmResult').mockRejectedValue(new ApiError('backend unreachable', 502, null));
    render(<BookingStatusPage />);

    expect(await screen.findByText('backend unreachable')).toBeInTheDocument();
  });

  it('cancels the booking and shows the cancelled state', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerCancel').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getCancelResult').mockResolvedValue({
      transaction_id: 'tx-1',
      cancelled_error: null,
      cancelled_order: { ...CONFIRMED_ORDER, status: 'CANCELLED' },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel booking' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Cancel booking' }));

    expect(await screen.findByText('This booking has been cancelled.')).toBeInTheDocument();
  });

  it('shows a failure state with retry when cancellation fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerCancel').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getCancelResult').mockResolvedValue({
      transaction_id: 'tx-1',
      cancelled_order: null,
      cancelled_error: { code: 'CANCEL_ERROR', message: 'Could not cancel' },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel booking' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Cancel booking' }));

    expect(await screen.findByText('Booking failed')).toBeInTheDocument();
  });
});
