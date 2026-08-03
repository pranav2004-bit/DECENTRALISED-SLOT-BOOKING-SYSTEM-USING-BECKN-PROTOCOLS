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

  it('livetracker3.md §6.1: shows both resources for a real Automotive multi-resource (bay+mechanic) booking', async () => {
    const multiResourceOrder = {
      ...CONFIRMED_ORDER,
      items: [{ id: 'bay-1' }, { id: 'mechanic-1' }],
      quote: {
        price: { currency: 'INR', value: '1200.00' },
        breakup: [
          { item: { id: 'bay-1' }, title: 'Bay 1', price: { currency: 'INR', value: '800.00' } },
          { item: { id: 'mechanic-1' }, title: 'Mechanic John', price: { currency: 'INR', value: '400.00' } },
        ],
      },
    };
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: multiResourceOrder,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Bay 1 + Mechanic John')).toBeInTheDocument());
    expect(screen.getByText('₹1,200.00')).toBeInTheDocument();
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

  it('reschedules a single-resource booking to a new time', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerUpdate').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getUpdateResult').mockResolvedValue({
      transaction_id: 'tx-1',
      updated_error: null,
      updated_order: {
        id: 'booking-1',
        status: 'ACTIVE',
        provider: { id: 'provider-1' },
        items: [{ id: 'item-1' }],
        fulfillments: [
          { id: 'booking-1', stops: [{ type: 'start', time: { timestamp: '2026-08-05T11:00:00+00:00' } }] },
        ],
      },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Reschedule' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Reschedule' }));
    await user.type(screen.getByLabelText('New date and time'), '2026-08-05T11:00');
    await user.click(screen.getByRole('button', { name: 'Confirm new time' }));

    expect(await screen.findByText('This booking has been rescheduled.')).toBeInTheDocument();
    expect(bookingApi.triggerUpdate).toHaveBeenCalledWith('tx-1', expect.any(String));
  });

  it('livetracker6.md §2.1: reschedules a real Automotive multi-resource (bay+mechanic) booking, moving both together', async () => {
    const user = userEvent.setup();
    const multiResourceOrder = {
      ...CONFIRMED_ORDER,
      items: [{ id: 'bay-1' }, { id: 'mechanic-1' }],
      quote: {
        price: { currency: 'INR', value: '1200.00' },
        breakup: [
          { item: { id: 'bay-1' }, title: 'Bay 1', price: { currency: 'INR', value: '800.00' } },
          { item: { id: 'mechanic-1' }, title: 'Mechanic John', price: { currency: 'INR', value: '400.00' } },
        ],
      },
    };
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: multiResourceOrder,
    });
    vi.spyOn(bookingApi, 'triggerUpdate').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getUpdateResult').mockResolvedValue({
      transaction_id: 'tx-1',
      updated_error: null,
      // dispatch_on_update's own real shape: every booking in the group gets its
      // own fulfillment entry, all moved to the same new requested time together.
      updated_order: {
        id: 'bay-1',
        status: 'ACTIVE',
        provider: { id: 'provider-1' },
        items: [{ id: 'bay-1' }, { id: 'mechanic-1' }],
        fulfillments: [
          { id: 'bay-1', stops: [{ type: 'start', time: { timestamp: '2026-08-05T11:00:00+00:00' } }] },
          { id: 'mechanic-1', stops: [{ type: 'start', time: { timestamp: '2026-08-05T11:00:00+00:00' } }] },
        ],
      },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Bay 1 + Mechanic John')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Reschedule' }));
    await user.type(screen.getByLabelText('New date and time'), '2026-08-05T11:00');
    await user.click(screen.getByRole('button', { name: 'Confirm new time' }));

    expect(await screen.findByText('This booking has been rescheduled.')).toBeInTheDocument();
    // The combined-name display is untouched by the reschedule — only the time moved.
    expect(screen.getByText('Bay 1 + Mechanic John')).toBeInTheDocument();
  });

  it('shows a failure state with retry when rescheduling fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerUpdate').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getUpdateResult').mockResolvedValue({
      transaction_id: 'tx-1',
      updated_order: null,
      updated_error: { code: 'SLOT_UNAVAILABLE', message: 'No matching slot for the requested time' },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Reschedule' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Reschedule' }));
    await user.type(screen.getByLabelText('New date and time'), '2026-08-05T11:00');
    await user.click(screen.getByRole('button', { name: 'Confirm new time' }));

    expect(await screen.findByText('Booking failed')).toBeInTheDocument();
  });

  it('does not offer to reschedule a CANCELLED booking', async () => {
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: { ...CONFIRMED_ORDER, status: 'CANCELLED' },
    });
    vi.spyOn(bookingApi, 'getStatusResult').mockResolvedValue({
      transaction_id: 'tx-1',
      status_order: { ...CONFIRMED_ORDER, status: 'CANCELLED' },
      status_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Haircut')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Reschedule' })).not.toBeInTheDocument();
  });

  it('does not show a rating prompt for a still-ACTIVE booking', async () => {
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Haircut')).toBeInTheDocument());
    expect(screen.queryByText('Rate this booking')).not.toBeInTheDocument();
  });

  it('does not show a rating prompt for a CANCELLED booking', async () => {
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: { ...CONFIRMED_ORDER, status: 'CANCELLED' },
    });
    vi.spyOn(bookingApi, 'getStatusResult').mockResolvedValue({
      transaction_id: 'tx-1',
      status_order: { ...CONFIRMED_ORDER, status: 'CANCELLED' },
      status_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Haircut')).toBeInTheDocument());
    expect(screen.queryByText('Rate this booking')).not.toBeInTheDocument();
  });

  it('shows a rating prompt for a COMPLETE booking and submitting produces a real, visible confirmation', async () => {
    const user = userEvent.setup();
    const completedOrder = { ...CONFIRMED_ORDER, status: 'COMPLETE' };
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: completedOrder,
    });
    vi.spyOn(bookingApi, 'getStatusResult').mockResolvedValue({
      transaction_id: 'tx-1',
      status_order: completedOrder,
      status_error: null,
    });
    vi.spyOn(bookingApi, 'triggerRating').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getRatingResult').mockResolvedValue({
      transaction_id: 'tx-1',
      rating_result: {},
      rating_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Rate this booking')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Rate 5 stars' }));

    expect(await screen.findByText('Thanks — you rated this 5 stars.')).toBeInTheDocument();
    expect(bookingApi.triggerRating).toHaveBeenCalledWith('tx-1', 'Order', '5');
  });

  it('lets the customer change their rating after submitting, and resubmits the new value', async () => {
    const user = userEvent.setup();
    const completedOrder = { ...CONFIRMED_ORDER, status: 'COMPLETE' };
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: completedOrder,
    });
    vi.spyOn(bookingApi, 'getStatusResult').mockResolvedValue({
      transaction_id: 'tx-1',
      status_order: completedOrder,
      status_error: null,
    });
    vi.spyOn(bookingApi, 'triggerRating').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getRatingResult').mockResolvedValue({
      transaction_id: 'tx-1',
      rating_result: {},
      rating_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Rate this booking')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Rate 3 stars' }));
    expect(await screen.findByText('Thanks — you rated this 3 stars.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Change rating' }));
    expect(screen.getByRole('button', { name: 'Rate 5 stars' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Rate 5 stars' }));

    expect(await screen.findByText('Thanks — you rated this 5 stars.')).toBeInTheDocument();
    expect(bookingApi.triggerRating).toHaveBeenLastCalledWith('tx-1', 'Order', '5');
  });

  it('shows a failure state with retry when rating submission fails', async () => {
    const user = userEvent.setup();
    const completedOrder = { ...CONFIRMED_ORDER, status: 'COMPLETE' };
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: completedOrder,
    });
    vi.spyOn(bookingApi, 'getStatusResult').mockResolvedValue({
      transaction_id: 'tx-1',
      status_order: completedOrder,
      status_error: null,
    });
    vi.spyOn(bookingApi, 'triggerRating').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getRatingResult').mockResolvedValue({
      transaction_id: 'tx-1',
      rating_result: null,
      rating_error: { code: 'RATING_ERROR', message: 'Could not submit' },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByText('Rate this booking')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Rate 3 stars' }));

    expect(await screen.findByText('Booking failed')).toBeInTheDocument();
  });

  it('requests support and displays the real returned contact info', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerSupport').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getSupportResult').mockResolvedValue({
      transaction_id: 'tx-1',
      support_result: { ref_id: 'booking-1', email: 'help@glowsalon.example' },
      support_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Get support' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Get support' }));

    expect(await screen.findByText('Email: help@glowsalon.example')).toBeInTheDocument();
  });

  it('displays a phone contact when that is what the real response returns', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerSupport').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getSupportResult').mockResolvedValue({
      transaction_id: 'tx-1',
      support_result: { ref_id: 'booking-1', phone: '+91-9876543210' },
      support_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Get support' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Get support' }));

    expect(await screen.findByText('Phone: +91-9876543210')).toBeInTheDocument();
    expect(screen.queryByText(/^Email:/)).not.toBeInTheDocument();
  });

  it('shows an honest fallback when the real response has no contact channel at all', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerSupport').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getSupportResult').mockResolvedValue({
      transaction_id: 'tx-1',
      support_result: { ref_id: 'booking-1' },
      support_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Get support' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Get support' }));

    expect(await screen.findByText('No contact info is available for this booking yet.')).toBeInTheDocument();
  });

  it('shows a failure state with retry when the support request fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerSupport').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getSupportResult').mockResolvedValue({
      transaction_id: 'tx-1',
      support_result: null,
      support_error: { code: 'SUPPORT_ERROR', message: 'Could not reach support' },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Get support' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Get support' }));

    expect(await screen.findByText('Booking failed')).toBeInTheDocument();
  });

  it('checks tracking and honestly shows an inactive status for a domain with no live feed', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerTrack').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getTrackResult').mockResolvedValue({
      transaction_id: 'tx-1',
      tracking: { status: 'inactive' },
      tracking_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check status' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Check status' }));

    expect(await screen.findByText('No live tracking update to show right now.')).toBeInTheDocument();
  });

  it('checks tracking and shows the real active state for a domain that has one', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerTrack').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getTrackResult').mockResolvedValue({
      transaction_id: 'tx-1',
      tracking: { status: 'active' },
      tracking_error: null,
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check status' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Check status' }));

    expect(
      await screen.findByText('Active — this booking’s fulfillment is currently in progress.')
    ).toBeInTheDocument();
  });

  it('shows a failure state with retry when checking tracking fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(bookingApi, 'getConfirmResult').mockResolvedValue({
      transaction_id: 'tx-1',
      confirmed_error: null,
      confirmed_order: CONFIRMED_ORDER,
    });
    vi.spyOn(bookingApi, 'triggerTrack').mockResolvedValue(undefined);
    vi.spyOn(bookingApi, 'getTrackResult').mockResolvedValue({
      transaction_id: 'tx-1',
      tracking: null,
      tracking_error: { code: 'TRACK_ERROR', message: 'Could not check' },
    });
    render(<BookingStatusPage />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Check status' })).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Check status' }));

    expect(await screen.findByText('Booking failed')).toBeInTheDocument();
  });
});
