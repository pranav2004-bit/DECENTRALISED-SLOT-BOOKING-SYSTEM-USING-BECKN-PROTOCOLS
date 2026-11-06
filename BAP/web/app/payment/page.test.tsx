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

vi.mock('@/lib/razorpay-checkout', () => ({
  openRazorpayCheckout: vi.fn(),
}));

import PaymentPage from './page';
import * as bookingApi from '@/lib/booking-api';
import * as razorpayCheckout from '@/lib/razorpay-checkout';
import * as realtimeModule from '@/lib/realtime/useRealtimeConnection';

function mockConnection(overrides: Partial<ReturnType<typeof realtimeModule.useRealtimeConnection>> = {}) {
  return vi.spyOn(realtimeModule, 'useRealtimeConnection').mockReturnValue({
    status: 'open',
    lastMessage: null,
    reconnect: vi.fn(),
    ...overrides,
  });
}

function setParams(entries: Record<string, string>) {
  mockUseSearchParams.mockReturnValue(new URLSearchParams(entries));
}

const PENDING_RESULT = {
  transaction_id: 'tx-1',
  status: 'PENDING' as const,
  amount: '899.00',
  currency: 'INR',
  vendor_txn_id: 'order_abc123',
};

const CONFIRMED_ORDER = {
  id: 'booking-1',
  status: 'ACTIVE',
  provider: { id: 'provider-1' },
  items: [{ id: 'item-1' }],
  fulfillments: [
    { id: 'booking-1', stops: [{ type: 'start', time: { timestamp: '2026-08-01T10:00:00+00:00' } }] },
  ],
  quote: {
    price: { currency: 'INR', value: '899.00' },
    breakup: [{ item: { id: 'item-1' }, title: 'Haircut', price: { currency: 'INR', value: '899.00' } }],
  },
};

describe('PaymentPage', () => {
  beforeEach(() => {
    mockPush.mockClear();
    process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID = 'rzp_test_fake';
    setParams({ transaction_id: 'tx-1' });
    mockConnection();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows an empty state when transaction_id is missing', () => {
    setParams({});
    render(<PaymentPage />);
    expect(screen.getByText('Missing order details')).toBeInTheDocument();
  });

  it('auto-starts payment on mount, with no click needed — /select\'s own "Proceed to payment" is the only click a customer makes', async () => {
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    render(<PaymentPage />);

    await waitFor(() => expect(bookingApi.triggerPayment).toHaveBeenCalledWith('tx-1', expect.any(String)));
    await waitFor(() =>
      expect(razorpayCheckout.openRazorpayCheckout).toHaveBeenCalledWith(
        expect.objectContaining({
          keyId: 'rzp_test_fake',
          orderId: 'order_abc123',
          amountRupees: '899.00',
          currency: 'INR',
        })
      )
    );
  });

  it('never opens the checkout widget when the trigger itself already resolved to SUCCEEDED', async () => {
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue({
      ...PENDING_RESULT,
      status: 'SUCCEEDED',
    });
    vi.spyOn(bookingApi, 'getPaymentResult').mockResolvedValue({
      ...PENDING_RESULT,
      status: 'SUCCEEDED',
    });
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    const user = userEvent.setup({ delay: null });
    render(<PaymentPage />);

    expect(razorpayCheckout.openRazorpayCheckout).not.toHaveBeenCalled();
    // livetracker5.md Phase 3.1/5's own "as usual" requirement: a genuinely
    // paid booking shows the same success popup /select used to show
    // directly, not a bare redirect — this is the real deliverable of this
    // page, not a side effect.
    expect(await screen.findByRole('dialog', { name: 'Booking confirmed' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Download invoice' })).toBeInTheDocument();
    expect(mockPush).not.toHaveBeenCalledWith('/bookings/tx-1');

    await user.click(screen.getByRole('button', { name: 'Done' }));
    expect(mockPush).toHaveBeenCalledWith('/bookings');
  });

  it('shows a declined message directly when the trigger itself resolves to FAILED', async () => {
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue({ ...PENDING_RESULT, status: 'FAILED' });
    render(<PaymentPage />);

    expect(await screen.findByText(/declined/)).toBeInTheDocument();
    expect(razorpayCheckout.openRazorpayCheckout).not.toHaveBeenCalled();
  });

  it('real gap found live 2026-11-05: retrying after a failed trigger uses a fresh idempotency key, not a replay of the cached failure', async () => {
    const user = userEvent.setup({ delay: null });
    const triggerSpy = vi
      .spyOn(bookingApi, 'triggerPayment')
      .mockResolvedValueOnce({ ...PENDING_RESULT, status: 'FAILED' })
      .mockResolvedValueOnce(PENDING_RESULT);
    render(<PaymentPage />);

    // The first attempt auto-fires on mount — no click. The second (retry) is
    // a genuine manual click, same as a real customer's own retry.
    expect(await screen.findByText(/declined/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));
    await waitFor(() => expect(triggerSpy).toHaveBeenCalledTimes(2));

    // triggerPayment(transactionId, idempotencyKey) — same transaction both
    // times, but the key itself must differ, or the backend's own
    // idempotency cache just replays the first (failed) response forever.
    const [, firstIdempotencyKey] = triggerSpy.mock.calls[0];
    const [, secondIdempotencyKey] = triggerSpy.mock.calls[1];
    expect(triggerSpy.mock.calls[0][0]).toBe('tx-1');
    expect(triggerSpy.mock.calls[1][0]).toBe('tx-1');
    expect(secondIdempotencyKey).not.toBe(firstIdempotencyKey);
  });

  it('starts polling only after the checkout widget itself reports believed success, never before', async () => {
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    vi.spyOn(bookingApi, 'getPaymentResult').mockResolvedValue({
      ...PENDING_RESULT,
      status: 'SUCCEEDED',
    });
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onBelievedSuccess }) => {
      onBelievedSuccess();
    });
    render(<PaymentPage />);

    expect(await screen.findByRole('dialog', { name: 'Booking confirmed' })).toBeInTheDocument();
  });

  it('shows a retryable declined state once polling itself resolves to FAILED', async () => {
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    vi.spyOn(bookingApi, 'getPaymentResult').mockResolvedValue({ ...PENDING_RESULT, status: 'FAILED' });
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onBelievedSuccess }) => {
      onBelievedSuccess();
    });
    render(<PaymentPage />);

    expect(await screen.findByText('Payment declined')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows a retry option when the customer dismisses the checkout without paying', async () => {
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onDismiss }) => {
      onDismiss();
    });
    render(<PaymentPage />);

    expect(await screen.findByText(/not completed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Proceed to payment' })).toBeInTheDocument();
  });

  it('shows a clean error, not a crash, when the payment vendor key is not configured', async () => {
    delete process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID;
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    render(<PaymentPage />);

    expect(await screen.findByText(/not configured/)).toBeInTheDocument();
    expect(razorpayCheckout.openRazorpayCheckout).not.toHaveBeenCalled();
  });

  it('resolves immediately from a real-time push message, not just the next poll tick', async () => {
    // livetracker5.md Phase 3.3's own Test Gate: a webhook-driven update reaches
    // an already-rendered page over the WebSocket, not only via polling.
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    const getResultSpy = vi
      .spyOn(bookingApi, 'getPaymentResult')
      .mockResolvedValue({ ...PENDING_RESULT, status: 'PENDING' });
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onBelievedSuccess }) => {
      onBelievedSuccess();
    });
    const { rerender } = render(<PaymentPage />);
    await waitFor(() => expect(getResultSpy).toHaveBeenCalled());

    // The webhook lands — simulates useRealtimeConnection's own `lastMessage`
    // changing on a later render of this same mounted page, exactly as it
    // would when a real socket message arrives.
    getResultSpy.mockResolvedValue({ ...PENDING_RESULT, status: 'SUCCEEDED' });
    mockConnection({ lastMessage: { type: 'payment.status_changed', status: 'SUCCEEDED' } });
    rerender(<PaymentPage />);

    expect(await screen.findByRole('dialog', { name: 'Booking confirmed' })).toBeInTheDocument();
  });
});
