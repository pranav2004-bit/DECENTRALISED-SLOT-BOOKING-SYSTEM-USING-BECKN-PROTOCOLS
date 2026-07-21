import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BookingFailedError, SessionExpiredError, SlotUnavailableError } from './BookingErrorStates';

describe('BookingFailedError', () => {
  it('renders the booking-failed message and retries', async () => {
    const onRetry = vi.fn();
    render(<BookingFailedError onRetry={onRetry} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Booking failed');
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe('SessionExpiredError', () => {
  it('renders the session-expired message with a Log in action', async () => {
    const onLogin = vi.fn();
    render(<SessionExpiredError onLogin={onLogin} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Session expired');
    await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(onLogin).toHaveBeenCalledOnce();
  });
});

describe('SlotUnavailableError', () => {
  it('renders the slot-unavailable message with a Choose another slot action', async () => {
    const onChooseAnother = vi.fn();
    render(<SlotUnavailableError onChooseAnother={onChooseAnother} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Slot no longer available');
    await userEvent.click(screen.getByRole('button', { name: 'Choose another slot' }));
    expect(onChooseAnother).toHaveBeenCalledOnce();
  });
});
