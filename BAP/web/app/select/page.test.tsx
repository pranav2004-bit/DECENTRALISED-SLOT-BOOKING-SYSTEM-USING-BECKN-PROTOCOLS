import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockUseSearchParams, mockPush } = vi.hoisted(() => ({
  mockUseSearchParams: vi.fn(),
  mockPush: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useSearchParams: mockUseSearchParams,
  useRouter: () => ({ push: mockPush }),
}));

import SelectPage from './page';
import * as bookingApi from '@/lib/booking-api';

function setParams(entries: Record<string, string>) {
  mockUseSearchParams.mockReturnValue(new URLSearchParams(entries));
}

describe('SelectPage', () => {
  beforeEach(() => {
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

  it('shows an empty state when required query params are missing', () => {
    setParams({});
    render(<SelectPage />);
    expect(screen.getByText('Missing selection details')).toBeInTheDocument();
  });

  it('reserves a slot and shows the confirmed time on success', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSelect').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getSelectResult').mockResolvedValue({
      transaction_id: 'tx-1',
      selected_error: null,
      selected_order: {
        provider: { id: 'provider-1' },
        items: [{ id: 'item-1' }],
        fulfillments: [{ id: 'booking-1', stops: [{ type: 'start', time: { timestamp: '2026-08-01T10:00:00+00:00' } }] }],
      },
    });
    render(<SelectPage />);

    const input = screen.getByLabelText('Date and time');
    fireEvent.change(input, { target: { value: '2026-08-01T10:00' } });
    await user.click(screen.getByRole('button', { name: 'Reserve this slot' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Checking availability');
    await waitFor(() => expect(screen.getByText(/Reserved:/)).toBeInTheDocument());
    expect(screen.getByRole('link', { name: 'Review order' })).toHaveAttribute(
      'href',
      expect.stringContaining('transaction_id=tx-1')
    );
  });

  it('shows SlotUnavailableError with a choose-another-time retry path', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerSelect').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getSelectResult').mockResolvedValue({
      transaction_id: 'tx-1',
      selected_order: null,
      selected_error: { code: 'SLOT_UNAVAILABLE', message: 'This slot was just booked' },
    });
    render(<SelectPage />);

    const input = screen.getByLabelText('Date and time');
    fireEvent.change(input, { target: { value: '2026-08-01T10:00' } });
    await user.click(screen.getByRole('button', { name: 'Reserve this slot' }));

    expect(await screen.findByText('Slot no longer available')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Choose another slot' }));
    expect(screen.getByLabelText('Date and time')).toBeInTheDocument();
  });

  it('shows a submit error when the trigger itself fails', async () => {
    const user = userEvent.setup({ delay: null });
    const { ApiError } = await import('@/lib/api-client');
    vi.spyOn(bookingApi, 'triggerSelect').mockRejectedValue(new ApiError('too many holds', 429, null));
    render(<SelectPage />);

    const input = screen.getByLabelText('Date and time');
    fireEvent.change(input, { target: { value: '2026-08-01T10:00' } });
    await user.click(screen.getByRole('button', { name: 'Reserve this slot' }));

    expect(await screen.findByText('too many holds')).toBeInTheDocument();
  });
});
