import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { mockUseSearchParams, mockPush, mockBack } = vi.hoisted(() => ({
  mockUseSearchParams: vi.fn(),
  mockPush: vi.fn(),
  mockBack: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useSearchParams: mockUseSearchParams,
  useRouter: () => ({ push: mockPush, back: mockBack }),
}));

import SelectPage from './page';
import * as bookingApi from '@/lib/booking-api';

function setParams(entries: Record<string, string>) {
  mockUseSearchParams.mockReturnValue(new URLSearchParams(entries));
}

/** The slot/hold is already placed by the time this page loads (search/page.tsx's
 * own inline BookMyShow-style selector creates it) — this page's required params
 * mirror exactly what that handoff carries forward, per app/select/page.tsx's own
 * `!transactionId || !itemId || !providerId || !slotStart` guard. */
const BASE_PARAMS = {
  transaction_id: 'tx-1',
  item_id: 'item-1',
  provider_id: 'provider-1',
  item_name: 'Haircut',
  provider_name: 'Glow Salon',
  slot_start: '2026-08-01T10:00:00+00:00',
  price_value: '500.00',
  price_currency: 'INR',
};

async function openDetailsForm(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Next' }));
  await user.type(screen.getByLabelText('Name'), 'Asha Rao');
  await user.type(screen.getByLabelText('Email ID'), 'asha@example.com');
  await user.type(screen.getByLabelText('Phone number'), '9876543210');
}

describe('SelectPage', () => {
  beforeEach(() => {
    setParams(BASE_PARAMS);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows an empty state when required query params are missing', () => {
    setParams({});
    render(<SelectPage />);
    expect(screen.getByText('Missing selection details')).toBeInTheDocument();
  });

  it('shows the booking summary from the handed-off params', () => {
    render(<SelectPage />);
    expect(screen.getByText('Glow Salon')).toBeInTheDocument();
    expect(screen.getByText('Haircut')).toBeInTheDocument();
    expect(screen.getByText('₹500')).toBeInTheDocument();
  });

  it('fills in customer details and proceeds to payment once init and confirm both succeed', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerInit').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getInitResult').mockResolvedValue({
      transaction_id: 'tx-1',
      init_error: null,
      init_order: { quote: { price: { currency: 'INR', value: '500.00' } } },
    });
    vi.spyOn(bookingApi, 'triggerConfirm').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: { id: 'booking-1' },
    });
    render(<SelectPage />);

    await openDetailsForm(user);
    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    await waitFor(() =>
      expect(bookingApi.triggerConfirm).toHaveBeenCalledWith(
        'tx-1',
        expect.any(String),
        { name: 'Asha Rao', email: 'asha@example.com', phone: '9876543210' }
      )
    );
    await waitFor(() =>
      expect(mockPush).toHaveBeenCalledWith(
        expect.stringMatching(/^\/payment\?.*transaction_id=tx-1/)
      )
    );
  });

  it('shows a clear message and does not proceed when the slot was just booked by someone else', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerInit').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getInitResult').mockResolvedValue({
      transaction_id: 'tx-1',
      init_order: null,
      init_error: { code: 'SLOT_UNAVAILABLE', message: 'This slot was just booked' },
    });
    render(<SelectPage />);

    await openDetailsForm(user);
    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(
      await screen.findByText(
        'Someone else just booked this exact time. Please go back and pick another slot.'
      )
    ).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('shows a multi-resource (bay + mechanic) booking summary using the already-joined item name', () => {
    setParams({ ...BASE_PARAMS, item_name: 'Bay 1 + Mechanic John' });
    render(<SelectPage />);
    expect(screen.getByText('Bay 1 + Mechanic John')).toBeInTheDocument();
  });

  it('shows a submit error when the trigger itself fails', async () => {
    const user = userEvent.setup({ delay: null });
    const { ApiError } = await import('@/lib/api-client');
    vi.spyOn(bookingApi, 'triggerInit').mockRejectedValue(new ApiError('too many holds', 429, null));
    render(<SelectPage />);

    await openDetailsForm(user);
    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(await screen.findByText('too many holds')).toBeInTheDocument();
  });
});
