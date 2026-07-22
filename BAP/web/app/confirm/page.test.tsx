import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockUseSearchParams, mockPush } = vi.hoisted(() => ({
  mockUseSearchParams: vi.fn(),
  mockPush: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useSearchParams: mockUseSearchParams,
  useRouter: () => ({ push: mockPush }),
}));

import ConfirmPage from './page';
import * as bookingApi from '@/lib/booking-api';

function setParams(entries: Record<string, string>) {
  mockUseSearchParams.mockReturnValue(new URLSearchParams(entries));
}

const QUOTE_ORDER = {
  provider: { id: 'provider-1' },
  items: [{ id: 'item-1' }],
  fulfillments: [
    { id: 'booking-1', stops: [{ type: 'start', time: { timestamp: '2026-08-01T10:00:00+00:00' } }] },
  ],
  quote: {
    price: { currency: 'INR', value: '500.00' },
    breakup: [{ item: { id: 'item-1' }, title: 'Haircut', price: { currency: 'INR', value: '500.00' } }],
    ttl: 'PT300S',
  },
};

describe('ConfirmPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    setParams({
      transaction_id: 'tx-1',
      item_id: 'item-1',
      item_name: 'Haircut',
      provider_name: 'Glow Salon',
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows an empty state when transaction_id is missing', () => {
    setParams({});
    render(<ConfirmPage />);
    expect(screen.getByText('Missing order details')).toBeInTheDocument();
  });

  it('triggers init automatically and shows the quotation once ready', async () => {
    vi.spyOn(bookingApi, 'triggerInit').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getInitResult').mockResolvedValue({
      transaction_id: 'tx-1',
      init_error: null,
      init_order: QUOTE_ORDER,
    });
    render(<ConfirmPage />);

    expect(await screen.findByRole('status')).toHaveTextContent('Preparing your order');
    await waitFor(() => expect(bookingApi.triggerInit).toHaveBeenCalledWith('tx-1'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm booking' })).toBeInTheDocument());
    expect(screen.getAllByText('Haircut').length).toBeGreaterThan(0);
    expect(screen.getByText('Total').closest('div')).toHaveTextContent('₹500.00');
  });

  it('shows SlotUnavailableError when init reports the hold is gone', async () => {
    vi.spyOn(bookingApi, 'triggerInit').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getInitResult').mockResolvedValue({
      transaction_id: 'tx-1',
      init_order: null,
      init_error: { code: 'SLOT_UNAVAILABLE', message: 'Hold expired' },
    });
    render(<ConfirmPage />);

    expect(await screen.findByText('Slot no longer available')).toBeInTheDocument();
  });

  it('confirms the booking and redirects to the booking-status screen', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerInit').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getInitResult').mockResolvedValue({
      transaction_id: 'tx-1',
      init_error: null,
      init_order: QUOTE_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerConfirm').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: { ...QUOTE_ORDER, id: 'booking-1', status: 'ACTIVE' },
    });
    render(<ConfirmPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm booking' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Confirm booking' }));

    expect(await screen.findByText(/Booking confirmed/)).toBeInTheDocument();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/bookings/tx-1?provider_name=Glow%20Salon'));
  });

  it('shows BookingFailedError with a retry when confirm fails for a non-slot reason', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerInit').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getInitResult').mockResolvedValue({
      transaction_id: 'tx-1',
      init_error: null,
      init_order: QUOTE_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerConfirm').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_order: null,
      confirmed_error: { code: 'PAYMENT_ERROR', message: 'Payment could not be processed' },
    });
    render(<ConfirmPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Confirm booking' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Confirm booking' }));

    expect(await screen.findByText('Booking failed')).toBeInTheDocument();
  });
});
