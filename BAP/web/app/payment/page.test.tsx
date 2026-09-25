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

  it('opens the checkout widget with the real server-resolved amount, never a client value', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    render(<PaymentPage />);

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

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
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue({
      ...PENDING_RESULT,
      status: 'SUCCEEDED',
    });
    vi.spyOn(bookingApi, 'getPaymentResult').mockResolvedValue({
      ...PENDING_RESULT,
      status: 'SUCCEEDED',
    });
    render(<PaymentPage />);

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(await screen.findByText(/Payment of/)).toBeInTheDocument();
    expect(razorpayCheckout.openRazorpayCheckout).not.toHaveBeenCalled();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/bookings/tx-1'));
  });

  it('shows a declined message directly when the trigger itself resolves to FAILED', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue({ ...PENDING_RESULT, status: 'FAILED' });
    render(<PaymentPage />);

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(await screen.findByText(/declined/)).toBeInTheDocument();
    expect(razorpayCheckout.openRazorpayCheckout).not.toHaveBeenCalled();
  });

  it('starts polling only after the checkout widget itself reports believed success, never before', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    vi.spyOn(bookingApi, 'getPaymentResult').mockResolvedValue({
      ...PENDING_RESULT,
      status: 'SUCCEEDED',
    });
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onBelievedSuccess }) => {
      onBelievedSuccess();
    });
    render(<PaymentPage />);

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(await screen.findByText(/Payment of/)).toBeInTheDocument();
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/bookings/tx-1'));
  });

  it('shows a retryable declined state once polling itself resolves to FAILED', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    vi.spyOn(bookingApi, 'getPaymentResult').mockResolvedValue({ ...PENDING_RESULT, status: 'FAILED' });
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onBelievedSuccess }) => {
      onBelievedSuccess();
    });
    render(<PaymentPage />);

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(await screen.findByText('Payment declined')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('shows a retry option when the customer dismisses the checkout without paying', async () => {
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onDismiss }) => {
      onDismiss();
    });
    render(<PaymentPage />);

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(await screen.findByText(/not completed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Proceed to payment' })).toBeInTheDocument();
  });

  it('shows a clean error, not a crash, when the payment vendor key is not configured', async () => {
    delete process.env.NEXT_PUBLIC_RAZORPAY_KEY_ID;
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    render(<PaymentPage />);

    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));

    expect(await screen.findByText(/not configured/)).toBeInTheDocument();
    expect(razorpayCheckout.openRazorpayCheckout).not.toHaveBeenCalled();
  });

  it('resolves immediately from a real-time push message, not just the next poll tick', async () => {
    // livetracker5.md Phase 3.3's own Test Gate: a webhook-driven update reaches
    // an already-rendered page over the WebSocket, not only via polling.
    const user = userEvent.setup({ delay: null });
    vi.spyOn(bookingApi, 'triggerPayment').mockResolvedValue(PENDING_RESULT);
    const getResultSpy = vi
      .spyOn(bookingApi, 'getPaymentResult')
      .mockResolvedValue({ ...PENDING_RESULT, status: 'PENDING' });
    vi.mocked(razorpayCheckout.openRazorpayCheckout).mockImplementation(async ({ onBelievedSuccess }) => {
      onBelievedSuccess();
    });
    const { rerender } = render(<PaymentPage />);
    await user.click(screen.getByRole('button', { name: 'Proceed to payment' }));
    await waitFor(() => expect(getResultSpy).toHaveBeenCalled());

    // The webhook lands — simulates useRealtimeConnection's own `lastMessage`
    // changing on a later render of this same mounted page, exactly as it
    // would when a real socket message arrives.
    getResultSpy.mockResolvedValue({ ...PENDING_RESULT, status: 'SUCCEEDED' });
    mockConnection({ lastMessage: { type: 'payment.status_changed', status: 'SUCCEEDED' } });
    rerender(<PaymentPage />);

    await waitFor(() => expect(mockPush).toHaveBeenCalledWith('/bookings/tx-1'));
  });
});
